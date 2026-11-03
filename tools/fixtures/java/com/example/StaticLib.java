package com.example;

public class StaticLib {
    static int counter;

    public static int bump(int by) {
        counter += by;
        return counter;
    }

    public static int get() {
        return counter;
    }
}
