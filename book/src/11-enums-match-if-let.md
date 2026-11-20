# Enums, match and `if let`

An enum is a value that is one of several variants. A variant carries either no payload or one
scalar payload. `match` selects a branch by variant, and `if let` selects one variant with a
shorter syntax.

## Declaration and construction

```rust
enum Op {
    Add,
    Mul(u32),
}
```

`Op::Add` has no payload; `Op::Mul(3)` carries a `u32`. A payload is a single scalar value — an
integer, `bool`, `str` or `ptr` — and a variant with a payload must be constructed with one.

## `match`

```rust,run
module enums

choose runtime

enum Op {
    Add,
    Mul(u32),
}

def apply(op: Op, n: u32) -> u32 {
    match op {
        Op::Add => { return n + 1; }
        Op::Mul(v) => { return n * v; }
    }
}

fn main() {
    print_num(apply(Op::Add, 41));
    print(" ");
    print_num(apply(Op::Mul(6), 7));
    print("\n");
}
```
<!-- expect: 42 42 -->

Three rules:

1. **An arm body is a block.** Write `Op::Add => { … }`, not `Op::Add => n + 1`.
2. **The match must cover every variant**, or end with `_ => { … }`.
3. **A payload binding is a new local** inside the arm. Give it a name distinct from the
   surrounding locals; the language has function scope.

The compiler rejects a missing variant and a duplicate pattern (E008).

## Patterns

Only two pattern forms exist:

| Pattern | Meaning |
|---|---|
| `E::V` / `E::V(name)` | match that variant; a payload variant binds a name |
| `_` | match anything |

There are no literal patterns, range patterns, struct patterns or tuple patterns. A match on an
enum is a dispatch over its variants, and a wildcard covers the rest.

## `if let`

`if let` handles one variant without writing a full match:

```rust,run
module iflet

choose runtime

enum Reply {
    Text(u32),
    Code(u32),
    Empty,
}

def describe(r: Reply) -> u32 {
    if let Reply::Code(n) = r {
        return n;
    }
    return 0;
}

fn main() {
    print_num(describe(Reply::Code(7)));
    print(" ");
    print_num(describe(Reply::Empty));
    print("\n");
}
```
<!-- expect: 7 0 -->

The `else` branch is optional. The binding (`n`) is available only inside the branch body.

## A state machine

Enums are the natural representation of a state machine. The state is a value, and each step takes
one state and returns the next:

```rust,run
module machine

choose runtime

enum State {
    Idle,
    Running(u32),
    Done,
}

def step(s: State) -> State {
    match s {
        State::Idle => { return State::Running(1); }
        State::Running(n) => {
            if n >= 2 {
                return State::Done;
            }
            return State::Running(n + 1);
        }
        State::Done => { return State::Done; }
    }
}

fn main() {
    let s: State = State::Idle;
    let a: State = step(s);
    let b: State = step(a);
    let c: State = step(b);
    match c {
        State::Done => { print("done\n"); }
        State::Running(n) => { print_num(n); print("\n"); }
        State::Idle => { print("idle\n"); }
    }
}
```
<!-- expect: done -->

The machine reaches `Done` after three steps. Each `step` consumes its state value and returns a
new one; there are no references and no shared mutable state.

## Enums with data

A payload lets a variant carry one value, which is enough for many protocols:

```rust
enum Event {
    Key(u32),
    Click(u32),
    Tick,
}
```

A variant that needs several values can carry a packed integer, or the enum can be paired with a
struct that holds the fields. The language does not have tuple variants, so a payload is always
one scalar.

## Matching the prelude enums

`Option<T>` and `Result<T, E>` are enums like any other:

```rust
match r {
    Result::Ok(v) => { return v; }
    Result::Err(e) => { return 0; }
}
```

A match on a generic enum uses the **plain** enum name. The checker rewrites it to the
monomorphised instance, and writing a type argument in the pattern is not part of the grammar.

## When to use an enum

Use an enum when the set of cases is closed and known to the compiler:

- a state machine;
- a token or event kind;
- a choice between two representations;
- an error with a code;
- `Option` and `Result`.

When the set is open, an integer tag with a table, or a struct with a kind field, is the usual
alternative. The enum's advantage is that `match` can prove every case is handled.

The next chapter uses the two predefined enums to carry failures.
