# Hello

The smallest runnable unit. `_start` is the process entry point; the `exit` syscall ends it.

```rust,run
module be_hello

choose runtime

use "helpers.lomt"

fn _start() {
    out("hello from Loment\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- expect: hello from Loment -->

- `module` is the first line and has no semicolon.
- `choose runtime` claims the runtime for the whole program; the entry unit is the only place that
  may write it.
- `_start` takes no arguments and **must not `return`** — end with `syscall4(60, …)`.
