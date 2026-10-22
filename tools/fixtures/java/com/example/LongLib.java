package com.example;

public class LongLib {
    public static long add(long a, long b) {
        return a + b;
    }

    public static int cmp(long a, long b) {
        if (a < b) {
            return -1;
        }
        if (a > b) {
            return 1;
        }
        return 0;
    }

    public static long mix(long a, int b) {
        long t = a * 3L + b;
        return t - (a / 2L);
    }
}
