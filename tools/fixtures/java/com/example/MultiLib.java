package com.example;

public class MultiLib {
    static int square(int x) {
        return x * x;
    }

    public static int sumSquares(int a, int b) {
        return square(a) + square(b);
    }

    static long twice(long x) {
        return x + x;
    }

    public static long addTwice(long a) {
        return twice(a) + twice(a);
    }
}
