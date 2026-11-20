# Control flow

Loment has four control-flow forms: `if`, `while`, the range `for`, and the collection `for`.
Tur 2026.2 adds `break` and `continue` to the loops. This chapter covers each form, the scoping
rule that surprises people, and the combinations that appear most often.

## Conditionals

```rust
if x > 0 {
    print("positive\n");
} else if x == 0 {
    print("zero\n");
} else {
    print("negative\n");
}
```

The condition must be `bool`. `&&` / `||` short-circuit, and Tur 2026.2 adds `and` / `or` / `not`
with the same meaning, plus `elif` as a shorter `else if`:

```rust
if x > 0 {
    print("positive\n");
} elif x == 0 {
    print("zero\n");
} else {
    print("negative\n");
}
```

`elif` and `else if` compile to the same code.

## `while`

```rust
let i: u32 = 0;
while i < 10 {
    i = i + 1;
}
```

The condition is checked before each iteration. `while true` with a `break` is the usual shape
for a loop whose exit condition is in the middle.

## Range `for`

```rust
for i in 0..n {
    // i = 0, 1, ..., n - 1
}
```

The upper bound is **exclusive**. The loop variable is created by the loop and has the type of
the bounds.

## Collection `for`

Tur 2026.2 adds a loop over a named array or a slice parameter:

```rust
let xs: [u32; 4] = [1, 2, 3, 4];
for x in xs {
    print_num(x);
}
```

`x` is a **copy** of each element. Writing to `x` does not change the array. The loop accepts a
named array or a `[T]` parameter; it does not accept an arbitrary expression, and there is no
`for i, x in xs` form.

## `break` and `continue`

Tur 2026.2 adds both, and they affect the **innermost** loop:

```rust,run
module loops

choose runtime

fn main() {
    let total: u32 = 0;
    for i in 0..10 {
        if i == 7 {
            break;
        }
        if i % 2 == 0 {
            continue;
        }
        total = total + i;
    }
    print_num(total);
    print("\n");
}
```
<!-- expect: 9 -->

The loop adds 1, 3 and 5, then stops at 7. `break` leaves the loop; `continue` jumps to the next
iteration. Both work in `while`, range `for` and collection `for`. Using either outside a loop is
a syntax error.

## Blocks do not scope `let`

This rule is easy to miss. A `let` inside an `if` branch is visible after the branch:

```rust,run
module scoping

choose runtime

fn main() {
    if true {
        let value: u32 = 7;
    }
    print_num(value);
    print("\n");
}
```
<!-- expect: 7 -->

The language has **function scope** for names. A block does not introduce a new scope for `let`,
so a name declared inside a branch lives until the end of the function. Two consequences:

- Do not rely on a block to hide a temporary; rename it instead.
- Two `let`s with the same name in one function are the same binding, and a second declaration is
  an error (E013). Give each one a distinct name.

Declarations in different functions are independent, and a loop body does not duplicate a
declaration per iteration; the name exists once for the function.

## A worked loop

A small number classifier uses every form:

```rust,run
module classify

choose runtime

def kind(n: u32) -> u32 {
    if n % 15 == 0 {
        return 3;
    } elif n % 3 == 0 {
        return 1;
    } elif n % 5 == 0 {
        return 2;
    }
    return 0;
}

fn main() {
    let counts: [u32; 4] = [0, 0, 0, 0];
    for i in 1..16 {
        let k: u32 = kind(i);
        counts[k] = counts[k] + 1;
    }
    print_num(counts[0]);
    print(" ");
    print_num(counts[1]);
    print(" ");
    print_num(counts[2]);
    print(" ");
    print_num(counts[3]);
    print("\n");
}
```
<!-- expect: 8 4 2 1 -->

The array is indexed by the classifier's result, and the loop covers 1 through 15. Every value
has one of four kinds, so the four counters account for all fifteen numbers.

## Choosing a loop

| Situation | Form |
|---|---|
| a fixed number of iterations | `for i in 0..n` |
| every element of an array or slice | `for x in xs` |
| stop when a condition changes | `while cond` |
| exit from the middle | `while true` + `break` |
| skip some elements | `continue` |
| a search with an early result | `while` + `break`, then use a flag or a `return` |

The next chapter introduces the first way to group values: the struct.
