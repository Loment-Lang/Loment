package com.example;

public class ExcLib {
    public static int thrower() {
        try {
            throw new RuntimeException();
        } catch (RuntimeException e) {
            return 7;
        }
    }

    public static int after() {
        return 3;
    }
}
