package com.example;

public class ArrayLib {
    public static int sum() {
        int[] a = new int[8];
        for (int i = 0; i < 8; i++) {
            a[i] = i * i;
        }
        int s = 0;
        for (int i = 0; i < 8; i++) {
            s += a[i];
        }
        return s;
    }

    public static long sumLong() {
        long[] a = new long[4];
        for (int i = 0; i < 4; i++) {
            a[i] = (long) i * i;
        }
        long s = 0;
        for (int i = 0; i < 4; i++) {
            s += a[i];
        }
        return s;
    }
}
