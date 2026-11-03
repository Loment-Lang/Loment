package com.example;

// A three-deep hierarchy with an override in each subclass. The closed-world
// dispatch criterion needs a receiver whose static type is the base class.
public class ShapeLib {
    public static class Shape {
        public int base;

        public Shape(int b) {
            base = b;
        }

        public int area() {
            return base;
        }
    }

    public static class Square extends Shape {
        public Square(int b) {
            super(b);
        }

        public int area() {
            return base * base;
        }
    }

    public static class Rect extends Shape {
        public Rect(int b) {
            super(b);
        }

        public int area() {
            return base * 2;
        }
    }

    public static int total(Shape a, Shape b) {
        return a.area() + b.area();
    }

    public static int run() {
        return total(new Square(3), new Rect(4));
    }

    public static int plain() {
        return total(new Shape(5), new Shape(6));
    }
}
