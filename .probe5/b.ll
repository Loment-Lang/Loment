; 由 tools/lomentc.py 生成 (native: LLVM IR, docs/144/145)
; clang -O1 driver.c this.ll -o exe

@loment.tur.edition = global [21 x i8] c"\4C\4F\4D\45\4E\54\2D\54\55\52\3A\74\75\72\32\30\32\36\2E\32\00"

; sum_all -> u32
define i32 @sum_all({ ptr, i64 } %xs) {
entry:
  %xs.addr = alloca { ptr, i64 }
  %s.addr = alloca i32
  %__tur2_i$1.addr = alloca i32
  %x.addr = alloca i32
  store { ptr, i64 } %xs, ptr %xs.addr
  store i32 0, ptr %s.addr
  store i32 0, ptr %__tur2_i$1.addr
  br label %L1_wcond
L1_wcond:
  %t1 = load i32, ptr %__tur2_i$1.addr
  %t2 = load { ptr, i64 }, ptr %xs.addr
  %t3 = extractvalue { ptr, i64 } %t2, 1
  %t4 = trunc i64 %t3 to i32
  %t5 = icmp ult i32 %t1, %t4
  br i1 %t5, label %L2_wbody, label %L3_wend
L2_wbody:
  %t6 = load { ptr, i64 }, ptr %xs.addr
  %t7 = extractvalue { ptr, i64 } %t6, 0
  %t8 = load i32, ptr %__tur2_i$1.addr
  %t9 = getelementptr inbounds i32, ptr %t7, i32 %t8
  %t10 = load i32, ptr %t9
  store i32 %t10, ptr %x.addr
  %t11 = load i32, ptr %s.addr
  %t12 = load i32, ptr %x.addr
  %t13 = add i32 %t11, %t12
  store i32 %t13, ptr %s.addr
  %t14 = load i32, ptr %__tur2_i$1.addr
  %t15 = add i32 %t14, 1
  store i32 %t15, ptr %__tur2_i$1.addr
  br label %L1_wcond
L3_wend:
  %t16 = load i32, ptr %s.addr
  ret i32 %t16
}
; main -> ()
define void @main() {
entry:
  ret void
}
