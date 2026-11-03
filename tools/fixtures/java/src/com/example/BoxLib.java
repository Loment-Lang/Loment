package com.example;

// Boxed primitives: `valueOf` allocates a box, the `*Value()` getters read it.
// Compiled with --release 17, so the string build is `invokedynamic` too.
public class BoxLib {
    public static int run() {
        Integer a = 7;
        Long b = -3L;
        Character c = 'x';
        String s = "" + a.intValue() + b.longValue() + c.charValue();
        System.out.println(s);
        return a.intValue() + (int) b.longValue();
    }
}
