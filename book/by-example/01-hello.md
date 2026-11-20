# Hello

The smallest runnable unit. `main` is the process entry point; `print` writes a string to stdout.

```rust,run
module be_hello

fn main() {
    print("hello from Loment\n");
}
```
<!-- expect: hello from Loment -->

- `module` is the first line and has no semicolon.
- `main` takes no arguments and has no return type; the linker's entry stub calls it and exits 0.
- `print` writes the string's bytes to stdout (one `write` syscall). It does not add a newline —
  the `\n` is in the string. There is no printf and no formatting.
- The freestanding entry is `_start` (kernel / no-libc code). A program with both uses `_start`.
