# Traits

A trait names methods; `impl Trait for Type` supplies them; calls dispatch statically.

```rust,run
module be_traits

choose runtime

use "helpers.lomt"

trait Measurable {
    fn measure(self) -> u32;
}

struct Small {
    v: u32,
}

struct Big {
    v: u32,
}

impl Measurable for Small {
    fn measure(self) -> u32 {
        return self.v;
    }
}

impl Measurable for Big {
    fn measure(self) -> u32 {
        return self.v * 10;
    }
}

fn _start() {
    let s: Small = Small { v: 7 };
    let b: Big = Big { v: 7 };
    dec(s.measure());
    out(" ");
    dec(b.measure());
    nl();
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: 7 70 -->

- `self` is the receiver; `self.v` reads its field.
- There are no trait objects (`dyn Trait`); a trait is a set of methods, not a value.
