# Result

Failures are values. `?` propagates one, in a `let`.

```rust,run
module be_result

choose runtime

use "helpers.lomt"

fn parse_digit(c: u32) -> Result<u32, u32> {
    if c >= 48 && c <= 57 {
        return Result::Ok(c - 48);
    }
    return Result::Err(1);
}

fn two_digits(a: u32, b: u32) -> Result<u32, u32> {
    let hi: u32 = parse_digit(a)?;
    let lo: u32 = parse_digit(b)?;
    return Result::Ok(hi * 10 + lo);
}

fn show(r: Result<u32, u32>) -> u32 {
    match r {
        Result::Ok(v) => { return v; }
        Result::Err(e) => { return 900 + e; }
    }
}

fn _start() {
    dec(show(two_digits(52, 55)));
    out(" ");
    dec(show(two_digits(52, 120)));
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 47 901 -->

- `?` belongs to a `let`: `let v: T = expr?;`.
- The enclosing function returns `Result`.
- `show` turns the enum into a number so the example can print it.
