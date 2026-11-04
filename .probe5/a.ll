; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

@.str.main.0 = private unnamed_addr constant [3 x i8] c"\6F\6B\0A"
@loment.tur.edition = global [21 x i8] c"\4C\4F\4D\45\4E\54\2D\54\55\52\3A\74\75\72\32\30\32\36\2E\32\00"

; main -> ()
define void @main() {
entry:
  %xs.addr = alloca [3 x i32]
  %sum.addr = alloca i32
  %__tur2_i.1.addr = alloca i32
  %x.addr = alloca i32
  %t1 = getelementptr inbounds [3 x i32], ptr %xs.addr, i32 0, i32 0
  store i32 10, ptr %t1
  %t2 = getelementptr inbounds [3 x i32], ptr %xs.addr, i32 0, i32 1
  store i32 20, ptr %t2
  %t3 = getelementptr inbounds [3 x i32], ptr %xs.addr, i32 0, i32 2
  store i32 30, ptr %t3
  store i32 0, ptr %sum.addr
  store i32 0, ptr %__tur2_i.1.addr
  br label %L1_wcond
L1_wcond:
  %t4 = load i32, ptr %__tur2_i.1.addr
  %t5 = icmp ult i32 %t4, 3
  br i1 %t5, label %L2_wbody, label %L3_wend
L2_wbody:
  %t6 = load i32, ptr %__tur2_i.1.addr
  %t7 = getelementptr inbounds [3 x i32], ptr %xs.addr, i32 0, i32 %t6
  %t8 = load i32, ptr %t7
  store i32 %t8, ptr %x.addr
  %t9 = load i32, ptr %sum.addr
  %t10 = load i32, ptr %x.addr
  %t11 = add i32 %t9, %t10
  store i32 %t11, ptr %sum.addr
  %t12 = load i32, ptr %__tur2_i.1.addr
  %t13 = add i32 %t12, 1
  store i32 %t13, ptr %__tur2_i.1.addr
  br label %L1_wcond
L3_wend:
  %t14 = getelementptr inbounds [3 x i8], ptr @.str.main.0, i64 0, i64 0
  %t15 = insertvalue { ptr, i64 } undef, ptr %t14, 0
  %t16 = insertvalue { ptr, i64 } %t15, i64 3, 1
  %t17 = extractvalue { ptr, i64 } %t16, 0
  %t18 = extractvalue { ptr, i64 } %t16, 1
  %t19 = ptrtoint ptr %t17 to i64
  %t20 = call i64 asm sideeffect "syscall", "={ax},{ax},{di},{si},{dx},~{cx},~{r11},~{memory}"(i64 1, i64 1, i64 %t19, i64 %t18)
  %t21 = trunc i64 %t20 to i32
  ret void
}
