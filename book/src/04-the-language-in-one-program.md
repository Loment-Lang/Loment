# The language in one program

This chapter is a map. It puts every core construct into one program so you can see their shapes
side by side, then points each line at the chapter that explains it. Read the program once for the
overview, and return to it when a later chapter introduces a construct you have already seen.

## The program

```rust,run
module tour

choose runtime

const LIMIT: u32 = 3;

struct Entry {
    cents: u32,
    tax: u32,
}

enum Kind {
    Small,
    Big(u32),
}

fn max_of<T>(a: T, b: T) -> T {
    if a > b {
        return a;
    }
    return b;
}

def total(e: Entry) -> u32 {
    return e.cents + e.tax;
}

def score(k: Kind) -> u32 {
    match k {
        Kind::Small => { return 1; }
        Kind::Big(w) => { return w * 2; }
    }
}

def sum_slice(xs: [u32]) -> u32 {
    let acc: u32 = 0;
    let i: u32 = 0;
    while i < slice_len(xs) {
        acc = acc + xs[i];
        i = i + 1;
    }
    return acc;
}

fn main() {
    let e: Entry = Entry { cents: 40, tax: 2 };
    let t: u32 = total(e);
    let s: u32 = score(Kind::Big(5));
    let a: u32 = 7;
    let b: u32 = 9;
    let m: u32 = max_of(a, b);
    let xs: [u32; 3] = [1, 2, 3];
    let sum: u32 = sum_slice(&xs);
    let acc: u32 = 0;
    for i in 0..LIMIT {
        acc = acc + i;
    }
    print_num(t);
    print(" ");
    print_num(s);
    print(" ");
    print_num(m);
    print(" ");
    print_num(sum);
    print(" ");
    print_num(acc);
    print("\n");
}
```
<!-- expect: 42 10 9 6 3 -->

The printed values are, in order: a struct total, an enum match, a generic maximum, a slice sum,
and a range loop. Each is a different part of the language, and each fits in a few lines.

## What each piece is

**`module tour`** names the unit. A unit is the compiler's input: one file, one module name.

**`choose runtime`** asks for the runtime block. `print_num`, `alloc`, `guard` and the collector
live there. Chapter 30 covers every project dimension.

**`const LIMIT: u32 = 3;`** declares a compile-time constant. The initialiser is one integer
literal; expressions are refused. Chapter 7 explains why.

**`struct Entry { … }`** declares a record. Fields are scalar values today, and a struct is a
value that moves. Chapter 10.

**`enum Kind { Small, Big(u32) }`** declares a tagged value. A variant carries zero or one
scalar payload. Chapter 11.

**`fn max_of<T>(a: T, b: T) -> T`** declares a generic function. The type parameter is
monomorphised at compile time. `def` is Tur 2026.2 sugar for `fn`, and generic declarations keep
the `fn` spelling today. Chapters 8 and 16.

**`match k { … }`** chooses an arm by variant and binds the payload. Arm bodies are blocks.
Chapter 11.

**`sum_slice(xs: [u32])`** takes a borrowed slice. The caller passes `&xs`; the array remains
usable afterwards. Chapter 13.

**`for i in 0..LIMIT`** iterates a range. The upper bound is exclusive. Chapter 9.

**`print_num`** writes an integer in decimal. `print` writes a string. Chapter 2.

## The same program in Tur 2026.1 spelling

Every construct above has an older spelling. The differences in this program are:

```rust
fn max_of<T>(a: T, b: T) -> T { … }     // fn instead of def
if a > b { … }                          // no and/or/not here
```

A Tur 2026.1 file also writes `while` and `if` exactly as above; the sugar is additive. What a
2026.1 file does not accept is `for x in xs`, `break`, `continue`, `defer`, `print_num`,
`orelse`, `catch`, and `lgc`. Chapter 5 explains the editions and chapter 20 covers the sugar.

## How to use the tour

Three ways work well:

1. **Read it once now.** You will not remember every construct, and you do not need to. The point
   is to know that a construct exists when a later chapter names it.
2. **Change one thing per run.** Replace `Kind::Big(5)` with `Kind::Small`, print `max_of` on
   `i64` values, or make `sum_slice` take a `mut [u32]`. The gate guarantees the original; your
   experiments are yours.
3. **Use it as a checklist before writing a larger program.** If a program needs a construct the
   tour does not show, find its chapter first.

The next chapter describes the editions that decide which spellings a file accepts.
