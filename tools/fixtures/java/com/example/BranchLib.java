package com.example;

public class BranchLib {
    public static int clamp(int x) {
        if (x < 0) {
            return 0;
        }
        return x;
    }

    public static int sign(int x) {
        if (x < 0) {
            return -1;
        }
        if (x > 0) {
            return 1;
        }
        return 0;
    }
}
