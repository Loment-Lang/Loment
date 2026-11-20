package com.example;

// Object arrays, `instanceof`, `checkcast`, and a virtual call on an array element.
public class CastLib {
    public static class Box {
        public int v;

        public Box(int v) {
            this.v = v;
        }

        public int get() {
            return v;
        }
    }

    public static int sum(Box[] xs) {
        int s = 0;
        for (int i = 0; i < xs.length; i++) {
            s += xs[i].get();
        }
        return s;
    }

    public static boolean isBox(Object o) {
        return o instanceof Box;
    }

    public static int cast(Object o) {
        Box b = (Box) o;
        return b.get();
    }

    public static int run() {
        Box[] xs = new Box[3];
        xs[0] = new Box(1);
        xs[1] = new Box(2);
        xs[2] = new Box(3);
        int s = sum(xs);
        Box b = (Box) xs[0];
        return s + (isBox(b) ? 10 : 0) + cast(xs[1]);
    }
}
