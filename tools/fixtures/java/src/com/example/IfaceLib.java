package com.example;

// Interface calls: `invokeinterface` on two implementations, closed-world dispatch.
public class IfaceLib {
    public interface Shape {
        int area();
    }

    public static class Sq implements Shape {
        public int b;

        public Sq(int b) {
            this.b = b;
        }

        public int area() {
            return b * b;
        }
    }

    public static class Re implements Shape {
        public int b;

        public Re(int b) {
            this.b = b;
        }

        public int area() {
            return b * 2;
        }
    }

    public static int total(Shape a, Shape b) {
        return a.area() + b.area();
    }

    public static int run() {
        return total(new Sq(3), new Re(4));
    }
}
