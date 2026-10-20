# Structs, enums and match

## Structs

```rust
struct Point {
    x: u32,
    y: u32,
}

fn dist2(p: Point) -> u32 {
    return p.x * p.x + p.y * p.y;
}

fn _start() {
    let p: Point = Point { x: 3, y: 4 };
    // dist2(p) == 25
}
```

Fields carry types; a literal names every field. Field access is `p.x`. Field *assignment* is not
an lvalue today (E014): to change a field, build a new literal with that field different.

Two limits are worth knowing before you design data around them:

- **Fields are scalars today** (`u8`…`u64`, `i8`…`i64`, `bool`, `ptr`, `str`). A field whose type
  is another struct or an enum is refused by the emitter ("struct … 字段类型 … 暂只支持标量").
- Up to **8 fields** per struct.

A struct is a value, and values **move**: passing it to a function, or assigning it to another
name, hands it over (E006 if you use the old name afterwards). Build a new literal when you need
a second copy. There are no self-referential structs; use `ptr` when you need indirection.

## Enums

```rust
enum Op {
    Add,
    Mul(u32),
}
```

A variant either carries **one** payload or none. Construction is `Op::Mul(3)`; a plain variant is
`Op::Add`. An enum can be passed and returned by value; its in-memory shape is a tag plus a payload
slot.

## match

```rust,run
module shapes

choose runtime

enum Op {
    Add,
    Mul(u32),
}

struct Point {
    x: u32,
    y: u32,
}

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let b: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(b, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(b, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(b, i) as u8;
        let hi: u8 = load8(b, n - 1 - i) as u8;
        store8(b, i, hi);
        store8(b, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, b as u64, n as u64);
}

fn apply(op: Op, n: u32) -> u32 {
    match op {
        Op::Add => { return n + 1; }
        Op::Mul(v) => { return n * v; }
    }
}

fn dist2(p: Point) -> u32 {
    return p.x * p.x + p.y * p.y;
}

fn _start() {
    let p: Point = Point { x: 3, y: 4 };
    write_dec(1, dist2(p));
    write_str(1, " ");
    write_dec(1, apply(Op::Add, 41));
    write_str(1, " ");
    write_dec(1, apply(Op::Mul(6), 7));
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 25 42 42 -->

Three rules:

1. **Arm bodies are blocks** — `Op::Add => { return n + 1; }`, not `Op::Add => n + 1`.
2. A match on an enum must cover every variant, or end with `_ => { … }`.
3. A payload binding (`v` in `Op::Mul(v)`) is a new local inside the arm. Give bindings distinct
   names from the surrounding locals.

## Using an enum as a small state machine

```rust
enum State {
    Idle,
    Running(u32),
    Done,
}

fn step(s: State) -> State {
    match s {
        State::Idle => { return State::Running(1); }
        State::Running(n) => {
            if n >= 3 {
                return State::Done;
            }
            return State::Running(n + 1);
        }
        State::Done => { return State::Done; }
    }
}
```

Because enums are values, the loop that drives this state machine passes the state in and takes the
next one back — no references, no lifetimes.
