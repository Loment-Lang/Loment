package com.example;

// Class initialisation order and laziness:
//   * B extends A, so touching B initialises A first;
//   * C is never touched, so its <clinit> must never run.
public class InitLib {
    public static class A {
        static int v = mark("A");
    }

    public static class B extends A {
        static int w = mark("B");
    }

    public static class C {
        static int z = mark("C");
    }

    static int mark(String s) {
        System.out.println(s);
        return 1;
    }

    public static int single() {
        return A.v;
    }

    public static int run() {
        return B.w;
    }
}
