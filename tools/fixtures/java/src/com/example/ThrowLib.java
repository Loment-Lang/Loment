package com.example;

// An exception that nobody catches: the build-time boundary prints and exits 1.
public class ThrowLib {
    public static int boom() {
        throw new RuntimeException();
    }

    public static int run() {
        int x = 1;
        return x + boom();
    }
}
