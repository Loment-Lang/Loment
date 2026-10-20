# Tests you can run

Loment has no test attributes and no testing framework in the language. What it has is a process
that returns an exit code, which is enough: a test binary is a normal program that runs checks and
exits non-zero on failure.

## The pattern

```rust,run
module selftest

choose runtime

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

fn twice(x: u32) -> u32 {
    return x * 2;
}

fn check(ok: bool, name: str) -> u32 {
    if ok {
        write_str(1, "PASS ");
        write_str(1, name);
        write_str(1, "\n");
        return 0;
    }
    write_str(1, "FAIL ");
    write_str(1, name);
    write_str(1, "\n");
    return 1;
}

fn _start() {
    let failed: u32 = 0;
    failed = failed + check(twice(3) == 6, "twice(3)");
    failed = failed + check(twice(0) == 0, "twice(0)");
    write_dec(1, 2 - failed);
    write_str(1, "/2 passed\n");
    syscall4(60, failed as u64, 0, 0);
}
```
<!-- expect: PASS twice(3)
PASS twice(0)
2/2 passed -->

The exit code is the number of failures, so any runner — a shell script, CI, `loment run` plus
`$?` — can tell pass from fail without parsing output.

## Running it

```console
$ loment run selftest.lomt; echo $?
PASS twice(3)
PASS twice(0)
2/2 passed
0
```

## Testing more than one unit

Keep every check in one entry unit and call into the library under test with `use`. The test
binary is the only unit that needs `_start`; libraries export ordinary `pub fn`s and are tested by
being called.

## In the repository checkout

The repository's own criteria are Python suites under `tools/`. They build units, run them and
compare bytes and exit codes; `python tools/ci.py --fast` runs the set. The development-only
`loment test` command (from the checkout's Python CLI, not the packaged `loment`) is a thin
wrapper that compiles a unit that defines `test_*` functions — useful, but the self-test pattern
above has no dependency on it and is what this book recommends for your own projects.
