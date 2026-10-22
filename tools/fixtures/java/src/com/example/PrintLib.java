package com.example;

/** A real javac fixture for the jrt printing slice (docs/281 §7). */
public class PrintLib {
    public static void hello() {
        System.out.println("hello");
        System.out.println(42);
        System.out.println(-7);
        System.out.print("x=");
        System.out.println(1234567890123L);
        System.out.println(Math.abs(-5));
        System.out.println(Math.min(3, 9));
        System.out.println(Math.max(3, 9));
        System.err.print("e");
        System.err.println();
    }
}
