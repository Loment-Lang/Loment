package com.example;

public class ClassA {
    public static int run() {
        ClassB b = new ClassB();
        b.x = 5;
        return b.x + ClassB.add(3, 4) + ClassB.value;
    }
}
