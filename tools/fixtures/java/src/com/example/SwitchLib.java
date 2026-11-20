package com.example;

// A dense switch (javac emits `tableswitch`) and a sparse one (`lookupswitch`).
public class SwitchLib {
    public static int pick(int x) {
        switch (x) {
            case 1: return 10;
            case 2: return 20;
            case 3: return 30;
            case 4: return 40;
            default: return -1;
        }
    }

    public static int sparse(int x) {
        switch (x) {
            case 1: return 100;
            case 1000: return 7;
            default: return 0;
        }
    }
}
