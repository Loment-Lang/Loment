package com.example;

public class StringLib {
    public static int len() {
        String s = "hello";
        return s.length();
    }

    public static int at() {
        String s = "hello";
        return s.charAt(1);
    }

    public static int code() {
        return "A".charAt(0);
    }
}
