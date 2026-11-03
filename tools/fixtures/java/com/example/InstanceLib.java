package com.example;

public class InstanceLib {
    int x;

    InstanceLib(int v) {
        x = v;
    }

    int add(int y) {
        return x + y;
    }

    public static int run() {
        InstanceLib o = new InstanceLib(7);
        return o.add(5);
    }
}
