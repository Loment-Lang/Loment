package com.example;

public class ObjLib {
    int x;
    long y;

    public static int makeAndRead() {
        ObjLib o = new ObjLib();
        o.x = 7;
        o.y = 9L;
        return o.x + (int) o.y;
    }
}
