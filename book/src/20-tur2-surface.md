# The Tur 2026.2 surface

Tur 2026.2 added a set of spellings that make everyday programs shorter. Each one has an older
equivalent, and each one compiles to the same code as that equivalent. This chapter covers them
as a set; later chapters give `defer`, `print_num` and `lgc` the space they need.

## `def`

```rust
def add(a: u32, b: u32) -> u32 {
    return a + b;
}
```

`def` is `fn`. The two declarations produce identical code. A generic declaration keeps the `fn`
spelling:

```rust
fn max_of<T>(a: T, b: T) -> T { … }     // correct
```

Use whichever reads better; the compiler does not distinguish them.

## `elif`

```rust
if x > 0 {
    print("positive\n");
} elif x == 0 {
    print("zero\n");
} else {
    print("negative\n");
}
```

`elif` is `else if`. The chain is the same conditional; only the keyword is shorter.

## `and`, `or`, `not`

```rust
if ready and not done { … }
if a > 0 or b > 0 { … }
```

They mean `&&`, `!` and `||`, with the same short-circuit. `and` and `or` accept `bool` operands,
and `not` takes one `bool`.

## `for x in xs`

```rust
let xs: [u32; 4] = [1, 2, 3, 4];
for x in xs {
    print_num(x);
}
```

The loop accepts a **named array** or a **slice parameter**. `x` is a copy of each element; writing
to `x` does not change the collection. Expressions, mutable references and `for i, x in xs` are
not accepted in this version.

A slice works the same way:

```rust
def total(xs: [u32]) -> u32 {
    let sum: u32 = 0;
    for x in xs {
        sum = sum + x;
    }
    return sum;
}
```

## `break` and `continue`

```rust
for i in 0..10 {
    if i == 7 {
        break;
    }
    if i % 2 == 0 {
        continue;
    }
    total = total + i;
}
```

Both affect the **innermost** loop. `break` leaves it; `continue` jumps to its update step. Using
either outside a loop is a syntax error. The emitter closes any collection epoch opened in the
body before jumping, so a break does not leak the epoch.

## Bare `return;`

In a unit function, `return;` exits early:

```rust
fn log_if(flag: bool) {
    if !flag {
        return;
    }
    print("flag set\n");
}
```

A function with `-> T` still requires a value: `return;` there is a syntax error.

## `orelse` and `catch`

```rust
let a: u32 = maybe() orelse 0;
let b: u32 = risky() catch 0;
```

They take the payload of a two-variant enum or evaluate a lazy fallback. Chapter 12 covers the
restriction: the enum has two variants and the first carries the payload.

## `print_num`

```rust
print_num(n);
```

Decimal output, optional minus sign, no newline, any integer type. It lives in the runtime block,
so it needs `choose runtime`. Chapter 21 covers the details.

## `defer`

```rust
defer free(p);
```

The call runs when the enclosing block exits. Chapter 21 covers the ordering and the limits.

## One program with all of it

```rust,run
module tur2

choose runtime

def classify(n: u32) -> u32 {
    if n == 0 {
        return 0;
    } elif n % 3 == 0 and n % 5 == 0 {
        return 3;
    } elif n % 3 == 0 {
        return 1;
    } elif n % 5 == 0 {
        return 2;
    }
    return 0;
}

def count(xs: [u32]) -> u32 {
    let seen: u32 = 0;
    for x in xs {
        if x == 0 {
            continue;
        }
        if x > 15 {
            break;
        }
        seen = seen + 1;
    }
    return seen;
}

fn main() {
    let xs: [u32; 6] = [3, 5, 15, 0, 20, 9];
    let sum: u32 = 0;
    for x in xs {
        sum = sum + classify(x);
    }
    print_num(sum);
    print(" ");
    print_num(count(&xs));
    print("\n");
}
```
<!-- expect: 9 3 -->

`classify` uses `elif`, `and` and `or`; `count` uses `for x in xs`, `continue` and `break`. Both
compile to the same code as the 2026.1 spellings.

## Compatibility

A Tur 2026.1 file treats every word in this chapter as an ordinary identifier. An undeclared file
that uses one of them as a declaration name is refused with E040, because it would change meaning
under the newest edition. Chapter 5 covers the rule and the two ways out: rename the identifier,
or pin the file to `tur2026.1`.

The next chapter gives `defer` and `print_num` their full treatment.
