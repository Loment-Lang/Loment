package com.example;

// Compiled with --release 17, so `+` becomes `invokedynamic` through
// StringConcatFactory. The recipe carries the literals; the holes are the
// operands in order.
public class ConcatLib {
    public static int run() {
        int a = 7;
        long b = -3L;
        char c = 'x';
        String s = "a=" + a + " b=" + b + " c=" + c;
        System.out.println(s);
        return s.length();
    }
}
