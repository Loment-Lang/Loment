# Structs

A struct groups named fields into one value. It is the first way this book has shown to build a
type of your own, and it behaves like the scalar types: a struct moves when it is passed or
assigned, and a field is read-only.

## Declaration and literal

```rust
struct Point {
    x: u32,
    y: u32,
}
```

A literal names every field:

```rust
let p: Point = Point { x: 3, y: 4 };
```

Fields are read with `.`:

```rust
let sum: u32 = p.x + p.y;
```

The field order in the literal does not have to match the declaration. Every field must appear
exactly once; a missing field or a duplicate is E015.

## A complete example

```rust,run
module points

choose runtime

struct Point {
    x: u32,
    y: u32,
}

def dist2(p: Point) -> u32 {
    return p.x * p.x + p.y * p.y;
}

def shifted(p: Point, dx: u32, dy: u32) -> Point {
    let q: Point = Point { x: p.x + dx, y: p.y + dy };
    return q;
}

fn main() {
    let p: Point = Point { x: 3, y: 4 };
    let q: Point = shifted(p, 1, 1);
    print_num(q.x);
    print(" ");
    print_num(q.y);
    print(" ");
    let r: Point = Point { x: 3, y: 4 };
    print_num(dist2(r));
    print("\n");
}
```
<!-- expect: 4 5 25 -->

`shifted(p, …)` moves `p`. The program builds a second literal `r` for the distance call because
`p` is no longer usable after the move. That is the rule, and it is checked (E006).

## Fields are read-only

A field is not an assignment target:

```rust
let p: Point = Point { x: 1, y: 2 };
p.x = 5;                  // E014: assignment target is not an lvalue
```

Build a new literal instead:

```rust
let q: Point = Point { x: 5, y: p.y };
```

The pattern keeps value semantics visible: `p` keeps its original fields, and `q` is a separate
value. A function that needs to change a field returns a new struct:

```rust
def moved(p: Point, dx: u32) -> Point {
    let q: Point = Point { x: p.x + dx, y: p.y };
    return q;
}
```

## Fields are scalars

A field can be an integer, `bool`, `str` or `ptr`:

```rust
struct Record {
    id: u32,
    name: str,
    live: bool,
    data: ptr,
}
```

A field whose type is another struct, an enum or an array is refused at build time; the error
names the field and the supported set. When a record needs a nested value, put the nested data
behind a `ptr` you manage, or flatten the fields into one struct.

A struct holds at most **8 fields**.

## Value semantics

A struct moves when it is passed or assigned:

```rust
fn consume(p: Point) -> u32 {
    return p.x;
}

fn caller() {
    let a: Point = Point { x: 1, y: 2 };
    let b: Point = a;        // b receives the value; a is moved
    let n: u32 = consume(b); // b is moved
}
```

There is no implicit copy of an aggregate. If you need two independent values, build two
literals. If you need shared mutation, pass a pointer and make the sharing explicit.

A moved name cannot be reassigned:

```rust
let p: Point = Point { x: 1, y: 2 };
let q: Point = p;
p = Point { x: 3, y: 4 };     // E006: p was moved
```

Use a fresh name for the second value.

## A struct as a result

Returning a struct is the usual way to return several values. Accumulate in scalars, then build
the struct once:

```rust,run
module stats

choose runtime

struct Stats {
    count: u32,
    total: u32,
}

def accumulate(values: [u32]) -> Stats {
    let count: u32 = 0;
    let total: u32 = 0;
    for v in values {
        count = count + 1;
        total = total + v;
    }
    let s: Stats = Stats { count: count, total: total };
    return s;
}

fn main() {
    let xs: [u32; 4] = [3, 1, 4, 2];
    let r: Stats = accumulate(&xs);
    print_num(r.count);
    print(" ");
    print_num(r.total);
    print("\n");
}
```
<!-- expect: 4 10 -->

The loop state lives in two scalars, which can be reassigned freely. The struct is constructed at
the end, so no field update and no double move is needed.

## Structs and behaviour

A struct on its own has no methods. A plain function takes the struct:

```rust
def measure(p: Point) -> u32 {
    return p.x + p.y;
}
```

A trait adds methods that are dispatched at compile time:

```rust
trait Measurable {
    fn measure(self) -> u32;
}

impl Measurable for Point {
    fn measure(self) -> u32 {
        return self.x + self.y;
    }
}
```

Chapter 17 covers traits. Until then, the function form is the direct way to attach behaviour.

## Designing with structs

Three rules keep struct code readable:

1. **Keep fields scalar.** Nested data goes behind a pointer the struct owns.
2. **Return a new value instead of trying to update a field.** Fields are read-only, and a
   returned struct makes the data flow visible at the call site.
3. **Give each struct one job.** Eight unrelated fields are a sign that two types are hiding in
   one.

The next chapter adds the other aggregate: the enum.
