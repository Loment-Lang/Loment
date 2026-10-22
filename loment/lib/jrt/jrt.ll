; 由 tools/loment_jrt.py 从 loment/lib/jrt/jrt.lomt 生成 —— 不要手改
; jrt: the minimal Java runtime (docs/281 §7)

; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe


; ---- Loment freestanding 运行时 (M31: 无 libc) ----
define internal void @__jrt_abort() {
  call void @llvm.trap()
  unreachable
}

declare void @llvm.trap()

; jrt_alloc -> ptr
define ptr @jrt_alloc(i32 %n) {
entry:
  %n.addr = alloca i32
  %cur.addr = alloca i64
  %want.addr = alloca i64
  %got.addr = alloca i64
  store i32 %n, ptr %n.addr
  %t1 = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 12, i64 0, i64 0, i64 0)
  store i64 %t1, ptr %cur.addr
  %t2 = load i64, ptr %cur.addr
  %t3 = icmp sle i64 %t2, 0
  br i1 %t3, label %L1_then, label %L2_else
L1_then:
  %t4 = inttoptr i32 0 to ptr
  ret ptr %t4
L2_else:
  br label %L3_end
L3_end:
  %t5 = load i64, ptr %cur.addr
  %t6 = load i32, ptr %n.addr
  %t7 = zext i32 %t6 to i64
  %t8 = add i64 %t5, %t7
  %t9 = add i64 %t8, 15
  %t11 = icmp eq i64 16, 0
  br i1 %t11, label %L5_dtrap, label %L4_dok
L5_dtrap:
  call void @__jrt_abort()
  unreachable
L4_dok:
  %t10 = udiv i64 %t9, 16
  br label %L6_dend
L6_dend:
  %t12 = mul i64 %t10, 16
  store i64 %t12, ptr %want.addr
  %t13 = load i64, ptr %want.addr
  %t14 = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 12, i64 %t13, i64 0, i64 0)
  store i64 %t14, ptr %got.addr
  %t15 = load i64, ptr %got.addr
  %t16 = icmp slt i64 %t15, 0
  br i1 %t16, label %L7_then, label %L8_else
L7_then:
  %t17 = inttoptr i32 0 to ptr
  ret ptr %t17
L8_else:
  br label %L9_end
L9_end:
  %t18 = load i64, ptr %cur.addr
  %t19 = inttoptr i64 %t18 to ptr
  ret ptr %t19
}
; ld32 -> u32
define i32 @ld32(ptr %p, i32 %off) {
entry:
  %p.addr = alloca ptr
  %off.addr = alloca i32
  store ptr %p, ptr %p.addr
  store i32 %off, ptr %off.addr
  %t1 = load ptr, ptr %p.addr
  %t2 = load i32, ptr %off.addr
  %t3 = getelementptr i8, ptr %t1, i32 %t2
  %t4 = load i8, ptr %t3
  %t5 = zext i8 %t4 to i32
  %t6 = load ptr, ptr %p.addr
  %t7 = load i32, ptr %off.addr
  %t8 = add i32 %t7, 1
  %t9 = getelementptr i8, ptr %t6, i32 %t8
  %t10 = load i8, ptr %t9
  %t11 = zext i8 %t10 to i32
  %t12 = shl i32 %t11, 8
  %t13 = or i32 %t5, %t12
  %t14 = load ptr, ptr %p.addr
  %t15 = load i32, ptr %off.addr
  %t16 = add i32 %t15, 2
  %t17 = getelementptr i8, ptr %t14, i32 %t16
  %t18 = load i8, ptr %t17
  %t19 = zext i8 %t18 to i32
  %t20 = shl i32 %t19, 16
  %t21 = or i32 %t13, %t20
  %t22 = load ptr, ptr %p.addr
  %t23 = load i32, ptr %off.addr
  %t24 = add i32 %t23, 3
  %t25 = getelementptr i8, ptr %t22, i32 %t24
  %t26 = load i8, ptr %t25
  %t27 = zext i8 %t26 to i32
  %t28 = shl i32 %t27, 24
  %t29 = or i32 %t21, %t28
  ret i32 %t29
}
; ld64 -> u64
define i64 @ld64(ptr %p, i32 %off) {
entry:
  %p.addr = alloca ptr
  %off.addr = alloca i32
  %lo.addr = alloca i64
  %hi.addr = alloca i64
  store ptr %p, ptr %p.addr
  store i32 %off, ptr %off.addr
  %t1 = load ptr, ptr %p.addr
  %t2 = load i32, ptr %off.addr
  %t3 = call i32 @ld32(ptr %t1, i32 %t2)
  %t4 = zext i32 %t3 to i64
  store i64 %t4, ptr %lo.addr
  %t5 = load ptr, ptr %p.addr
  %t6 = load i32, ptr %off.addr
  %t7 = add i32 %t6, 4
  %t8 = call i32 @ld32(ptr %t5, i32 %t7)
  %t9 = zext i32 %t8 to i64
  store i64 %t9, ptr %hi.addr
  %t10 = load i64, ptr %lo.addr
  %t11 = load i64, ptr %hi.addr
  %t12 = shl i64 %t11, 32
  %t13 = or i64 %t10, %t12
  ret i64 %t13
}
; wr -> ()
define void @wr(i64 %fd, ptr %p, i32 %n) {
entry:
  %fd.addr = alloca i64
  %p.addr = alloca ptr
  %n.addr = alloca i32
  store i64 %fd, ptr %fd.addr
  store ptr %p, ptr %p.addr
  store i32 %n, ptr %n.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load ptr, ptr %p.addr
  %t3 = ptrtoint ptr %t2 to i64
  %t4 = load i32, ptr %n.addr
  %t5 = zext i32 %t4 to i64
  %t6 = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 1, i64 %t1, i64 %t3, i64 %t5)
  ret void
}
; dec -> u32
define i32 @dec(ptr %buf, i64 %v) {
entry:
  %buf.addr = alloca ptr
  %v.addr = alloca i64
  %x.addr = alloca i64
  %n.addr = alloca i32
  %i.addr = alloca i32
  %a.addr = alloca i32
  %b.addr = alloca i32
  store ptr %buf, ptr %buf.addr
  store i64 %v, ptr %v.addr
  %t1 = load i64, ptr %v.addr
  %t2 = icmp eq i64 %t1, 0
  br i1 %t2, label %L1_then, label %L2_else
L1_then:
  %t3 = load ptr, ptr %buf.addr
  %t4 = getelementptr i8, ptr %t3, i32 0
  store i8 48, ptr %t4
  ret i32 1
L2_else:
  br label %L3_end
L3_end:
  %t5 = load i64, ptr %v.addr
  store i64 %t5, ptr %x.addr
  store i32 0, ptr %n.addr
  br label %L4_wcond
L4_wcond:
  %t6 = load i64, ptr %x.addr
  %t7 = icmp ugt i64 %t6, 0
  br i1 %t7, label %L5_wbody, label %L6_wend
L5_wbody:
  %t8 = load ptr, ptr %buf.addr
  %t9 = load i32, ptr %n.addr
  %t10 = load i64, ptr %x.addr
  %t12 = icmp eq i64 10, 0
  br i1 %t12, label %L8_dtrap, label %L7_dok
L8_dtrap:
  call void @__jrt_abort()
  unreachable
L7_dok:
  %t11 = urem i64 %t10, 10
  br label %L9_dend
L9_dend:
  %t13 = add i64 48, %t11
  %t14 = trunc i64 %t13 to i8
  %t15 = getelementptr i8, ptr %t8, i32 %t9
  store i8 %t14, ptr %t15
  %t16 = load i64, ptr %x.addr
  %t18 = icmp eq i64 10, 0
  br i1 %t18, label %L11_dtrap, label %L10_dok
L11_dtrap:
  call void @__jrt_abort()
  unreachable
L10_dok:
  %t17 = udiv i64 %t16, 10
  br label %L12_dend
L12_dend:
  store i64 %t17, ptr %x.addr
  %t19 = load i32, ptr %n.addr
  %t20 = add i32 %t19, 1
  store i32 %t20, ptr %n.addr
  br label %L4_wcond
L6_wend:
  store i32 0, ptr %i.addr
  br label %L13_wcond
L13_wcond:
  %t21 = load i32, ptr %i.addr
  %t22 = load i32, ptr %n.addr
  %t24 = icmp eq i32 2, 0
  br i1 %t24, label %L17_dtrap, label %L16_dok
L17_dtrap:
  call void @__jrt_abort()
  unreachable
L16_dok:
  %t23 = udiv i32 %t22, 2
  br label %L18_dend
L18_dend:
  %t25 = icmp ult i32 %t21, %t23
  br i1 %t25, label %L14_wbody, label %L15_wend
L14_wbody:
  %t26 = load ptr, ptr %buf.addr
  %t27 = load i32, ptr %i.addr
  %t28 = getelementptr i8, ptr %t26, i32 %t27
  %t29 = load i8, ptr %t28
  %t30 = zext i8 %t29 to i32
  store i32 %t30, ptr %a.addr
  %t31 = load ptr, ptr %buf.addr
  %t32 = load i32, ptr %n.addr
  %t33 = sub i32 %t32, 1
  %t34 = load i32, ptr %i.addr
  %t35 = sub i32 %t33, %t34
  %t36 = getelementptr i8, ptr %t31, i32 %t35
  %t37 = load i8, ptr %t36
  %t38 = zext i8 %t37 to i32
  store i32 %t38, ptr %b.addr
  %t39 = load ptr, ptr %buf.addr
  %t40 = load i32, ptr %i.addr
  %t41 = load i32, ptr %b.addr
  %t42 = trunc i32 %t41 to i8
  %t43 = getelementptr i8, ptr %t39, i32 %t40
  store i8 %t42, ptr %t43
  %t44 = load ptr, ptr %buf.addr
  %t45 = load i32, ptr %n.addr
  %t46 = sub i32 %t45, 1
  %t47 = load i32, ptr %i.addr
  %t48 = sub i32 %t46, %t47
  %t49 = load i32, ptr %a.addr
  %t50 = trunc i32 %t49 to i8
  %t51 = getelementptr i8, ptr %t44, i32 %t48
  store i8 %t50, ptr %t51
  %t52 = load i32, ptr %i.addr
  %t53 = add i32 %t52, 1
  store i32 %t53, ptr %i.addr
  br label %L13_wcond
L15_wend:
  %t54 = load i32, ptr %n.addr
  ret i32 %t54
}
; num64 -> ()
define void @num64(i64 %fd, i64 %v, i32 %nl) {
entry:
  %fd.addr = alloca i64
  %v.addr = alloca i64
  %nl.addr = alloca i32
  %buf.addr = alloca ptr
  %neg.addr = alloca i32
  %x.addr = alloca i64
  %n.addr = alloca i32
  %o.addr = alloca i32
  %out.addr = alloca ptr
  %i.addr = alloca i32
  store i64 %fd, ptr %fd.addr
  store i64 %v, ptr %v.addr
  store i32 %nl, ptr %nl.addr
  %t1 = call ptr @jrt_alloc(i32 40)
  store ptr %t1, ptr %buf.addr
  store i32 0, ptr %neg.addr
  %t2 = load i64, ptr %v.addr
  store i64 %t2, ptr %x.addr
  %t3 = load i64, ptr %v.addr
  %t4 = icmp slt i64 %t3, 0
  br i1 %t4, label %L1_then, label %L2_else
L1_then:
  store i32 1, ptr %neg.addr
  %t5 = load i64, ptr %v.addr
  %t6 = sub i64 0, %t5
  store i64 %t6, ptr %x.addr
  br label %L3_end
L2_else:
  br label %L3_end
L3_end:
  %t7 = load ptr, ptr %buf.addr
  %t8 = load i64, ptr %x.addr
  %t9 = call i32 @dec(ptr %t7, i64 %t8)
  store i32 %t9, ptr %n.addr
  store i32 0, ptr %o.addr
  %t10 = call ptr @jrt_alloc(i32 48)
  store ptr %t10, ptr %out.addr
  %t11 = load i32, ptr %neg.addr
  %t12 = icmp eq i32 %t11, 1
  br i1 %t12, label %L4_then, label %L5_else
L4_then:
  %t13 = load ptr, ptr %out.addr
  %t14 = getelementptr i8, ptr %t13, i32 0
  store i8 45, ptr %t14
  store i32 1, ptr %o.addr
  br label %L6_end
L5_else:
  br label %L6_end
L6_end:
  store i32 0, ptr %i.addr
  br label %L7_wcond
L7_wcond:
  %t15 = load i32, ptr %i.addr
  %t16 = load i32, ptr %n.addr
  %t17 = icmp ult i32 %t15, %t16
  br i1 %t17, label %L8_wbody, label %L9_wend
L8_wbody:
  %t18 = load ptr, ptr %out.addr
  %t19 = load i32, ptr %o.addr
  %t20 = load i32, ptr %i.addr
  %t21 = add i32 %t19, %t20
  %t22 = load ptr, ptr %buf.addr
  %t23 = load i32, ptr %i.addr
  %t24 = getelementptr i8, ptr %t22, i32 %t23
  %t25 = load i8, ptr %t24
  %t26 = zext i8 %t25 to i32
  %t27 = trunc i32 %t26 to i8
  %t28 = getelementptr i8, ptr %t18, i32 %t21
  store i8 %t27, ptr %t28
  %t29 = load i32, ptr %i.addr
  %t30 = add i32 %t29, 1
  store i32 %t30, ptr %i.addr
  br label %L7_wcond
L9_wend:
  %t31 = load i32, ptr %o.addr
  %t32 = load i32, ptr %n.addr
  %t33 = add i32 %t31, %t32
  store i32 %t33, ptr %o.addr
  %t34 = load i32, ptr %nl.addr
  %t35 = icmp eq i32 %t34, 1
  br i1 %t35, label %L10_then, label %L11_else
L10_then:
  %t36 = load ptr, ptr %out.addr
  %t37 = load i32, ptr %o.addr
  %t38 = getelementptr i8, ptr %t36, i32 %t37
  store i8 10, ptr %t38
  %t39 = load i32, ptr %o.addr
  %t40 = add i32 %t39, 1
  store i32 %t40, ptr %o.addr
  br label %L12_end
L11_else:
  br label %L12_end
L12_end:
  %t41 = load i64, ptr %fd.addr
  %t42 = load ptr, ptr %out.addr
  %t43 = load i32, ptr %o.addr
  call void @wr(i64 %t41, ptr %t42, i32 %t43)
  ret void
}
; utf8 -> u32
define i32 @utf8(ptr %buf, i32 %c) {
entry:
  %buf.addr = alloca ptr
  %c.addr = alloca i32
  store ptr %buf, ptr %buf.addr
  store i32 %c, ptr %c.addr
  %t1 = load i32, ptr %c.addr
  %t2 = icmp ult i32 %t1, 128
  br i1 %t2, label %L1_then, label %L2_else
L1_then:
  %t3 = load ptr, ptr %buf.addr
  %t4 = load i32, ptr %c.addr
  %t5 = trunc i32 %t4 to i8
  %t6 = getelementptr i8, ptr %t3, i32 0
  store i8 %t5, ptr %t6
  ret i32 1
L2_else:
  br label %L3_end
L3_end:
  %t7 = load i32, ptr %c.addr
  %t8 = icmp ult i32 %t7, 2048
  br i1 %t8, label %L4_then, label %L5_else
L4_then:
  %t9 = load ptr, ptr %buf.addr
  %t10 = load i32, ptr %c.addr
  %t11 = lshr i32 %t10, 6
  %t12 = add i32 192, %t11
  %t13 = trunc i32 %t12 to i8
  %t14 = getelementptr i8, ptr %t9, i32 0
  store i8 %t13, ptr %t14
  %t15 = load ptr, ptr %buf.addr
  %t16 = load i32, ptr %c.addr
  %t18 = icmp eq i32 64, 0
  br i1 %t18, label %L8_dtrap, label %L7_dok
L8_dtrap:
  call void @__jrt_abort()
  unreachable
L7_dok:
  %t17 = urem i32 %t16, 64
  br label %L9_dend
L9_dend:
  %t19 = add i32 128, %t17
  %t20 = trunc i32 %t19 to i8
  %t21 = getelementptr i8, ptr %t15, i32 1
  store i8 %t20, ptr %t21
  ret i32 2
L5_else:
  br label %L6_end
L6_end:
  %t22 = load ptr, ptr %buf.addr
  %t23 = load i32, ptr %c.addr
  %t24 = lshr i32 %t23, 12
  %t25 = add i32 224, %t24
  %t26 = trunc i32 %t25 to i8
  %t27 = getelementptr i8, ptr %t22, i32 0
  store i8 %t26, ptr %t27
  %t28 = load ptr, ptr %buf.addr
  %t29 = load i32, ptr %c.addr
  %t30 = lshr i32 %t29, 6
  %t32 = icmp eq i32 64, 0
  br i1 %t32, label %L11_dtrap, label %L10_dok
L11_dtrap:
  call void @__jrt_abort()
  unreachable
L10_dok:
  %t31 = urem i32 %t30, 64
  br label %L12_dend
L12_dend:
  %t33 = add i32 128, %t31
  %t34 = trunc i32 %t33 to i8
  %t35 = getelementptr i8, ptr %t28, i32 1
  store i8 %t34, ptr %t35
  %t36 = load ptr, ptr %buf.addr
  %t37 = load i32, ptr %c.addr
  %t39 = icmp eq i32 64, 0
  br i1 %t39, label %L14_dtrap, label %L13_dok
L14_dtrap:
  call void @__jrt_abort()
  unreachable
L13_dok:
  %t38 = urem i32 %t37, 64
  br label %L15_dend
L15_dend:
  %t40 = add i32 128, %t38
  %t41 = trunc i32 %t40 to i8
  %t42 = getelementptr i8, ptr %t36, i32 2
  store i8 %t41, ptr %t42
  ret i32 3
}
; jstr -> ()
define void @jstr(i64 %fd, ptr %s, i32 %nl) {
entry:
  %fd.addr = alloca i64
  %s.addr = alloca ptr
  %nl.addr = alloca i32
  %nb.addr = alloca ptr
  %data.addr = alloca ptr
  %n.addr = alloca i32
  %buf.addr = alloca ptr
  %o.addr = alloca i32
  %i.addr = alloca i32
  %c0.addr = alloca i32
  %hi.addr = alloca i32
  %c.addr = alloca i32
  store i64 %fd, ptr %fd.addr
  store ptr %s, ptr %s.addr
  store i32 %nl, ptr %nl.addr
  %t1 = load ptr, ptr %s.addr
  %t2 = inttoptr i32 0 to ptr
  %t3 = icmp eq ptr %t1, %t2
  br i1 %t3, label %L1_then, label %L2_else
L1_then:
  %t4 = call ptr @jrt_alloc(i32 8)
  store ptr %t4, ptr %nb.addr
  %t5 = load ptr, ptr %nb.addr
  %t6 = getelementptr i8, ptr %t5, i32 0
  store i8 110, ptr %t6
  %t7 = load ptr, ptr %nb.addr
  %t8 = getelementptr i8, ptr %t7, i32 1
  store i8 117, ptr %t8
  %t9 = load ptr, ptr %nb.addr
  %t10 = getelementptr i8, ptr %t9, i32 2
  store i8 108, ptr %t10
  %t11 = load ptr, ptr %nb.addr
  %t12 = getelementptr i8, ptr %t11, i32 3
  store i8 108, ptr %t12
  %t13 = load i32, ptr %nl.addr
  %t14 = icmp eq i32 %t13, 1
  br i1 %t14, label %L4_then, label %L5_else
L4_then:
  %t15 = load ptr, ptr %nb.addr
  %t16 = getelementptr i8, ptr %t15, i32 4
  store i8 10, ptr %t16
  %t17 = load i64, ptr %fd.addr
  %t18 = load ptr, ptr %nb.addr
  call void @wr(i64 %t17, ptr %t18, i32 5)
  br label %L6_end
L5_else:
  %t19 = load i64, ptr %fd.addr
  %t20 = load ptr, ptr %nb.addr
  call void @wr(i64 %t19, ptr %t20, i32 4)
  br label %L6_end
L6_end:
  br label %L3_end
L2_else:
  %t21 = load ptr, ptr %s.addr
  %t22 = call i64 @ld64(ptr %t21, i32 8)
  %t23 = inttoptr i64 %t22 to ptr
  store ptr %t23, ptr %data.addr
  %t24 = load ptr, ptr %s.addr
  %t25 = call i32 @ld32(ptr %t24, i32 16)
  store i32 %t25, ptr %n.addr
  %t26 = call ptr @jrt_alloc(i32 1024)
  store ptr %t26, ptr %buf.addr
  store i32 0, ptr %o.addr
  store i32 0, ptr %i.addr
  br label %L7_wcond
L7_wcond:
  %t27 = load i32, ptr %i.addr
  %t28 = load i32, ptr %n.addr
  %t29 = icmp ult i32 %t27, %t28
  br i1 %t29, label %L8_wbody, label %L9_wend
L8_wbody:
  %t30 = load ptr, ptr %data.addr
  %t31 = load i32, ptr %i.addr
  %t32 = mul i32 %t31, 2
  %t33 = getelementptr i8, ptr %t30, i32 %t32
  %t34 = load i8, ptr %t33
  %t35 = zext i8 %t34 to i32
  store i32 %t35, ptr %c0.addr
  %t36 = load ptr, ptr %data.addr
  %t37 = load i32, ptr %i.addr
  %t38 = mul i32 %t37, 2
  %t39 = add i32 %t38, 1
  %t40 = getelementptr i8, ptr %t36, i32 %t39
  %t41 = load i8, ptr %t40
  %t42 = zext i8 %t41 to i32
  store i32 %t42, ptr %hi.addr
  %t43 = load i32, ptr %c0.addr
  %t44 = load i32, ptr %hi.addr
  %t45 = shl i32 %t44, 8
  %t46 = or i32 %t43, %t45
  store i32 %t46, ptr %c.addr
  %t47 = load i32, ptr %o.addr
  %t48 = add i32 %t47, 4
  %t49 = icmp ugt i32 %t48, 1024
  br i1 %t49, label %L10_then, label %L11_else
L10_then:
  %t50 = load i64, ptr %fd.addr
  %t51 = load ptr, ptr %buf.addr
  %t52 = load i32, ptr %o.addr
  call void @wr(i64 %t50, ptr %t51, i32 %t52)
  store i32 0, ptr %o.addr
  br label %L12_end
L11_else:
  br label %L12_end
L12_end:
  %t53 = load i32, ptr %c.addr
  %t54 = icmp ult i32 %t53, 128
  br i1 %t54, label %L13_then, label %L14_else
L13_then:
  %t55 = load ptr, ptr %buf.addr
  %t56 = load i32, ptr %o.addr
  %t57 = load i32, ptr %c.addr
  %t58 = trunc i32 %t57 to i8
  %t59 = getelementptr i8, ptr %t55, i32 %t56
  store i8 %t58, ptr %t59
  %t60 = load i32, ptr %o.addr
  %t61 = add i32 %t60, 1
  store i32 %t61, ptr %o.addr
  br label %L15_end
L14_else:
  %t62 = load i32, ptr %c.addr
  %t63 = icmp ult i32 %t62, 2048
  br i1 %t63, label %L16_then, label %L17_else
L16_then:
  %t64 = load ptr, ptr %buf.addr
  %t65 = load i32, ptr %o.addr
  %t66 = load i32, ptr %c.addr
  %t67 = lshr i32 %t66, 6
  %t68 = add i32 192, %t67
  %t69 = trunc i32 %t68 to i8
  %t70 = getelementptr i8, ptr %t64, i32 %t65
  store i8 %t69, ptr %t70
  %t71 = load ptr, ptr %buf.addr
  %t72 = load i32, ptr %o.addr
  %t73 = add i32 %t72, 1
  %t74 = load i32, ptr %c.addr
  %t76 = icmp eq i32 64, 0
  br i1 %t76, label %L20_dtrap, label %L19_dok
L20_dtrap:
  call void @__jrt_abort()
  unreachable
L19_dok:
  %t75 = urem i32 %t74, 64
  br label %L21_dend
L21_dend:
  %t77 = add i32 128, %t75
  %t78 = trunc i32 %t77 to i8
  %t79 = getelementptr i8, ptr %t71, i32 %t73
  store i8 %t78, ptr %t79
  %t80 = load i32, ptr %o.addr
  %t81 = add i32 %t80, 2
  store i32 %t81, ptr %o.addr
  br label %L18_end
L17_else:
  %t82 = load ptr, ptr %buf.addr
  %t83 = load i32, ptr %o.addr
  %t84 = load i32, ptr %c.addr
  %t85 = lshr i32 %t84, 12
  %t86 = add i32 224, %t85
  %t87 = trunc i32 %t86 to i8
  %t88 = getelementptr i8, ptr %t82, i32 %t83
  store i8 %t87, ptr %t88
  %t89 = load ptr, ptr %buf.addr
  %t90 = load i32, ptr %o.addr
  %t91 = add i32 %t90, 1
  %t92 = load i32, ptr %c.addr
  %t93 = lshr i32 %t92, 6
  %t95 = icmp eq i32 64, 0
  br i1 %t95, label %L23_dtrap, label %L22_dok
L23_dtrap:
  call void @__jrt_abort()
  unreachable
L22_dok:
  %t94 = urem i32 %t93, 64
  br label %L24_dend
L24_dend:
  %t96 = add i32 128, %t94
  %t97 = trunc i32 %t96 to i8
  %t98 = getelementptr i8, ptr %t89, i32 %t91
  store i8 %t97, ptr %t98
  %t99 = load ptr, ptr %buf.addr
  %t100 = load i32, ptr %o.addr
  %t101 = add i32 %t100, 2
  %t102 = load i32, ptr %c.addr
  %t104 = icmp eq i32 64, 0
  br i1 %t104, label %L26_dtrap, label %L25_dok
L26_dtrap:
  call void @__jrt_abort()
  unreachable
L25_dok:
  %t103 = urem i32 %t102, 64
  br label %L27_dend
L27_dend:
  %t105 = add i32 128, %t103
  %t106 = trunc i32 %t105 to i8
  %t107 = getelementptr i8, ptr %t99, i32 %t101
  store i8 %t106, ptr %t107
  %t108 = load i32, ptr %o.addr
  %t109 = add i32 %t108, 3
  store i32 %t109, ptr %o.addr
  br label %L18_end
L18_end:
  br label %L15_end
L15_end:
  %t110 = load i32, ptr %i.addr
  %t111 = add i32 %t110, 1
  store i32 %t111, ptr %i.addr
  br label %L7_wcond
L9_wend:
  %t112 = load i32, ptr %nl.addr
  %t113 = icmp eq i32 %t112, 1
  br i1 %t113, label %L28_then, label %L29_else
L28_then:
  %t114 = load ptr, ptr %buf.addr
  %t115 = load i32, ptr %o.addr
  %t116 = getelementptr i8, ptr %t114, i32 %t115
  store i8 10, ptr %t116
  %t117 = load i32, ptr %o.addr
  %t118 = add i32 %t117, 1
  store i32 %t118, ptr %o.addr
  br label %L30_end
L29_else:
  br label %L30_end
L30_end:
  %t119 = load i32, ptr %o.addr
  %t120 = icmp ugt i32 %t119, 0
  br i1 %t120, label %L31_then, label %L32_else
L31_then:
  %t121 = load i64, ptr %fd.addr
  %t122 = load ptr, ptr %buf.addr
  %t123 = load i32, ptr %o.addr
  call void @wr(i64 %t121, ptr %t122, i32 %t123)
  br label %L33_end
L32_else:
  br label %L33_end
L33_end:
  br label %L3_end
L3_end:
  ret void
}
; copy8 -> u32
define i32 @copy8(ptr %dst, i32 %doff, ptr %src, i32 %soff, i32 %n) {
entry:
  %dst.addr = alloca ptr
  %doff.addr = alloca i32
  %src.addr = alloca ptr
  %soff.addr = alloca i32
  %n.addr = alloca i32
  %i.addr = alloca i32
  store ptr %dst, ptr %dst.addr
  store i32 %doff, ptr %doff.addr
  store ptr %src, ptr %src.addr
  store i32 %soff, ptr %soff.addr
  store i32 %n, ptr %n.addr
  store i32 0, ptr %i.addr
  br label %L1_wcond
L1_wcond:
  %t1 = load i32, ptr %i.addr
  %t2 = load i32, ptr %n.addr
  %t3 = icmp ult i32 %t1, %t2
  br i1 %t3, label %L2_wbody, label %L3_wend
L2_wbody:
  %t4 = load ptr, ptr %dst.addr
  %t5 = load i32, ptr %doff.addr
  %t6 = load i32, ptr %i.addr
  %t7 = add i32 %t5, %t6
  %t8 = load ptr, ptr %src.addr
  %t9 = load i32, ptr %soff.addr
  %t10 = load i32, ptr %i.addr
  %t11 = add i32 %t9, %t10
  %t12 = getelementptr i8, ptr %t8, i32 %t11
  %t13 = load i8, ptr %t12
  %t14 = zext i8 %t13 to i32
  %t15 = trunc i32 %t14 to i8
  %t16 = getelementptr i8, ptr %t4, i32 %t7
  store i8 %t15, ptr %t16
  %t17 = load i32, ptr %i.addr
  %t18 = add i32 %t17, 1
  store i32 %t18, ptr %i.addr
  br label %L1_wcond
L3_wend:
  %t19 = load i32, ptr %n.addr
  ret i32 %t19
}
; jrt_print_i32 -> ()
define void @jrt_print_i32(i64 %fd, i32 %v) {
entry:
  %fd.addr = alloca i64
  %v.addr = alloca i32
  store i64 %fd, ptr %fd.addr
  store i32 %v, ptr %v.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load i32, ptr %v.addr
  %t3 = sext i32 %t2 to i64
  call void @num64(i64 %t1, i64 %t3, i32 0)
  ret void
}
; jrt_println_i32 -> ()
define void @jrt_println_i32(i64 %fd, i32 %v) {
entry:
  %fd.addr = alloca i64
  %v.addr = alloca i32
  store i64 %fd, ptr %fd.addr
  store i32 %v, ptr %v.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load i32, ptr %v.addr
  %t3 = sext i32 %t2 to i64
  call void @num64(i64 %t1, i64 %t3, i32 1)
  ret void
}
; jrt_print_i64 -> ()
define void @jrt_print_i64(i64 %fd, i64 %v) {
entry:
  %fd.addr = alloca i64
  %v.addr = alloca i64
  store i64 %fd, ptr %fd.addr
  store i64 %v, ptr %v.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load i64, ptr %v.addr
  call void @num64(i64 %t1, i64 %t2, i32 0)
  ret void
}
; jrt_println_i64 -> ()
define void @jrt_println_i64(i64 %fd, i64 %v) {
entry:
  %fd.addr = alloca i64
  %v.addr = alloca i64
  store i64 %fd, ptr %fd.addr
  store i64 %v, ptr %v.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load i64, ptr %v.addr
  call void @num64(i64 %t1, i64 %t2, i32 1)
  ret void
}
; jrt_print_s -> ()
define void @jrt_print_s(i64 %fd, ptr %s) {
entry:
  %fd.addr = alloca i64
  %s.addr = alloca ptr
  store i64 %fd, ptr %fd.addr
  store ptr %s, ptr %s.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load ptr, ptr %s.addr
  call void @jstr(i64 %t1, ptr %t2, i32 0)
  ret void
}
; jrt_println_s -> ()
define void @jrt_println_s(i64 %fd, ptr %s) {
entry:
  %fd.addr = alloca i64
  %s.addr = alloca ptr
  store i64 %fd, ptr %fd.addr
  store ptr %s, ptr %s.addr
  %t1 = load i64, ptr %fd.addr
  %t2 = load ptr, ptr %s.addr
  call void @jstr(i64 %t1, ptr %t2, i32 1)
  ret void
}
; jrt_println -> ()
define void @jrt_println(i64 %fd) {
entry:
  %fd.addr = alloca i64
  %b.addr = alloca ptr
  store i64 %fd, ptr %fd.addr
  %t1 = call ptr @jrt_alloc(i32 4)
  store ptr %t1, ptr %b.addr
  %t2 = load ptr, ptr %b.addr
  %t3 = getelementptr i8, ptr %t2, i32 0
  store i8 10, ptr %t3
  %t4 = load i64, ptr %fd.addr
  %t5 = load ptr, ptr %b.addr
  call void @wr(i64 %t4, ptr %t5, i32 1)
  ret void
}
