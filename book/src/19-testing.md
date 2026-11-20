# Tests you can run

Loment has no test attributes and no testing framework in the language. What it has is a process
that returns an exit code, which is enough: a test binary is a normal program that runs checks and
exits with the number of failures.

## The pattern

```rust,run
module selftest

choose runtime

def twice(x: u32) -> u32 {
    return x * 2;
}

def check(ok: bool, name: str) -> u32 {
    if ok {
        print("PASS ");
        print(name);
        print("\n");
        return 0;
    }
    print("FAIL ");
    print(name);
    print("\n");
    return 1;
}

fn main() {
    let failed: u32 = 0;
    failed = failed + check(twice(3) == 6, "twice(3)");
    failed = failed + check(twice(0) == 0, "twice(0)");
    print_num(2 - failed);
    print("/2 passed\n");
    syscall4(60, failed as u64, 0, 0);
}
```
<!-- expect: PASS twice(3)
PASS twice(0)
2/2 passed -->

The exit code is the failure count. A runner can tell pass from fail without parsing output:

```console
$ loment run selftest.lomt; echo $?
PASS twice(3)
PASS twice(0)
2/2 passed
0
```

## A check that explains itself

A boolean is enough for pass/fail, and a failure usually needs the value that was wrong. Print the
value on failure:

```rust
def check_eq(actual: u32, expected: u32, name: str) -> u32 {
    if actual == expected {
        print("PASS ");
        print(name);
        print("\n");
        return 0;
    }
    print("FAIL ");
    print(name);
    print(": expected ");
    print_num(expected);
    print(", got ");
    print_num(actual);
    print("\n");
    return 1;
}
```

The helper is a normal function. Share it across every test in the unit, or copy it into a small
test library when several units need it.

## Testing a function with many cases

A table of cases is two parallel arrays plus a loop:

```rust,run
module table

choose runtime

def classify(n: u32) -> u32 {
    if n == 0 {
        return 0;
    } elif n % 2 == 0 {
        return 2;
    }
    return 1;
}

fn main() {
    let inputs: [u32; 5] = [0, 1, 2, 3, 4];
    let want: [u32; 5] = [0, 1, 2, 1, 2];
    let failed: u32 = 0;
    for i in 0..5 {
        let got: u32 = classify(inputs[i]);
        if got != want[i] {
            print("FAIL case ");
            print_num(i);
            print("\n");
            failed = failed + 1;
        }
    }
    print_num(5 - failed);
    print("/5 passed\n");
    syscall4(60, failed as u64, 0, 0);
}
```
<!-- expect: 5/5 passed -->

The two arrays are parallel: `inputs[i]` goes with `want[i]`. The loop keeps the index explicit,
and a failure names the case. When the table grows past a few dozen entries, loading cases from a
file at run time is the next step; the test binary stays the same shape.

## Testing a library

Keep the checks in one entry unit and call the library with `use`:

```rust
module mathx_test

choose runtime

use mathx

fn main() {
    // call mathx's pub functions
}
```

The test binary is the only unit that needs `main` (or `_start`). The library exports ordinary
functions and is tested by being called. Any `test_*` functions inside it are optional
self-checks, not a framework.

## What a test can observe

A test can observe:

- the return value of a function;
- the bytes a function writes to a buffer;
- the process exit code;
- output written to standard output, if the runner captures it.

A test cannot observe a private name, a compiler optimisation, or the exact machine code. Those
belong to the repository's own criteria (`tools/*_test.py`), which build units and compare IR,
bytes and behaviour. The contribution book covers that layer.

## Running tests

For a single unit:

```console
$ loment run selftest.lomt
$ echo $?
```

For a project, a shell script that runs each test binary and stops on the first non-zero status is
enough:

```console
$ for t in tests/*.lomt; do loment run "$t" || exit 1; done
```

The repository's development-only `loment test` command is a thin wrapper around units that define
`test_*` functions. The pattern in this chapter has no dependency on it and works with the
installed package.

## Rules of thumb

1. **One behaviour per check.** A failing check names the rule.
2. **Exit with the failure count.** Zero means success; anything else is a report.
3. **Test the boundary values.** Zero, the maximum, the empty container, and one past the edge.
4. **Keep tests deterministic.** No clock, no network, no filesystem unless the test is about
   those things.
5. **A test that cannot fail is not a test.** Write the check so that the wrong implementation
   produces a non-zero exit.

The next part covers the syntax that Tur 2026.2 and 2026.3 add.
