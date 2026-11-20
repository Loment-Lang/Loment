# Enums and match

A variant carries at most one payload. `match` arms are blocks and must be exhaustive.

```rust,run
module be_enums

choose runtime

use "helpers.lomt"

enum Shape {
    Dot,
    Circle(u32),
    Square(u32),
}

fn area(s: Shape) -> u32 {
    match s {
        Shape::Dot => { return 0; }
        Shape::Circle(r) => { return 3 * r * r; }
        Shape::Square(side) => { return side * side; }
    }
}

fn _start() {
    dec(area(Shape::Dot));
    out(" ");
    dec(area(Shape::Circle(4)));
    out(" ");
    dec(area(Shape::Square(6)));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 0 48 36 -->

- A variant carries **one** payload. `Rect(u32, u32)` is not expressible: use a struct for the
  pair, or two variants.
- The arm body is `{ … }`. `=> expr` is a parse error.
- Pass the value to a function and match the parameter. **Binding names share the function
  scope** — do not reuse the parameter's name (`Square(s)` inside `fn area(s: Shape)` shadows it).
