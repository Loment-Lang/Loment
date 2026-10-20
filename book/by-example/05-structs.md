# Structs

A struct is a value: fields are scalars, and passing it copies it.

```rust,run
module be_structs

choose runtime

use "helpers.lomt"

struct Point {
    x: u32,
    y: u32,
}

struct Rect {
    w: u32,
    h: u32,
}

fn area(r: Rect) -> u32 {
    return r.w * r.h;
}

fn shifted(p: Point, dx: u32) -> Point {
    let q: Point = Point { x: p.x + dx, y: p.y };
    return q;
}

fn _start() {
    let r: Rect = Rect { w: 6, h: 7 };
    let copy: Rect = Rect { w: r.w, h: r.h };
    dec(area(r));
    out(" ");
    let p: Point = Point { x: 3, y: 4 };
    let q: Point = shifted(p, 10);
    dec(q.x);
    out(" ");
    dec(area(copy));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 42 13 42 -->

- A struct is a value, and values **move**: `let copy: Rect = r;` would hand `r` over, and passing
  a struct to a function moves it — after `shifted(p, 10)`, `p` is gone (E006). To keep both,
  build a new literal from the fields *before* the move, as `copy` does here.
- **Field assignment is not an lvalue today**: `q.x = 7` is E014. Build a new struct literal with
  the changed field.
- Fields are scalars today; a struct field cannot be another struct or an enum.
- A struct literal is not an expression: bind it to a `let` first, then return the binding.
