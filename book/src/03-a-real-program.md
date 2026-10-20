# A real program: wc in Loment

Every language book needs a program with input, output and a loop. This one reads standard input
and prints three numbers: lines, words, bytes — a `wc -l -w -c` in miniature.

## The whole program

```rust,run
module wc

choose runtime

fn write_str(fd: u64, s: str) -> i64 {
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}

fn write_dec(fd: u64, v: u32) {
    let buf: ptr = alloc(12);
    let n: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        n = 1;
    }
    while x > 0 {
        store8(buf, n, (48 + x % 10) as u8);
        x = x / 10;
        n = n + 1;
    }
    let i: u32 = 0;
    while i < n / 2 {
        let lo: u8 = load8(buf, i) as u8;
        let hi: u8 = load8(buf, n - 1 - i) as u8;
        store8(buf, i, hi);
        store8(buf, n - 1 - i, lo);
        i = i + 1;
    }
    syscall4(1, fd, buf as u64, n as u64);
}

fn is_space(c: u32) -> bool {
    if c == 32 || c == 9 || c == 10 || c == 13 {
        return true;
    }
    return false;
}

fn _start() {
    let buf: ptr = alloc(4096);
    let lines: u32 = 0;
    let words: u32 = 0;
    let bytes: u64 = 0;
    let inword: u32 = 0;
    let more: u32 = 1;
    while more == 1 {
        let n: i64 = syscall4(0, 0, buf as u64, 4096);
        if n <= 0 {
            more = 0;
        } else {
            let k: u32 = n as u32;
            bytes = bytes + (n as u64);
            let i: u32 = 0;
            while i < k {
                let ch: u32 = load8(buf, i) as u32;
                if ch == 10 {
                    lines = lines + 1;
                }
                if is_space(ch) {
                    inword = 0;
                } else {
                    if inword == 0 {
                        words = words + 1;
                        inword = 1;
                    }
                }
                i = i + 1;
            }
        }
    }
    write_dec(1, lines);
    write_str(1, " ");
    write_dec(1, words);
    write_str(1, " ");
    write_dec(1, bytes as u32);
    write_str(1, "\n");
    syscall4(60, 0, 0, 0);
}
```
<!-- stdin: one two\nthree\n -->
<!-- expect: 2 3 14 -->

Save it as `wc.lomt` and run it:

```console
$ printf 'one two\nthree\n' | loment run wc.lomt
2 3 14
```

## What to notice

**There is no `break`.** The read loop keeps a flag (`more`) and lets the condition end the loop.
Loment has `return`, but a `return` leaves the function; to leave a loop early you restructure the
condition. This is deliberate and it shows up everywhere: the project's own tools are written in
this shape.

**Read in chunks.** `alloc(4096)` hands out a small buffer and the loop fills it again for every
block of input; the counters live across iterations. The heap is a fixed size, so "read the whole
file into memory" is not the default shape here — streaming is.

**Pointers are arithmetic.** `(buf as u64) + off` converts the pointer, adds, and passes the result
to the kernel. There is no pointer-typed `+`; conversion is explicit.

**Bytes are `load8`.** A file is bytes; `load8(buf, i)` reads one, and comparing it to `10` (LF)
is a line count. `is_space` treats space, tab, LF and CR as separators, the same four bytes `wc`
does. `inword` carries the word state from one chunk to the next, so a word split across a chunk
boundary still counts once.

**Functions come before or after their callers.** There are no forward declarations. A name must
exist somewhere in the unit; the checker resolves it.

**`choose runtime` is a declaration, not a suggestion.** This program uses `%` and `/`, which need
the runtime's division-by-zero trap. In the default `no_runtime` mode the compiler refuses the
program and names the construct. The first line after `module` claims the runtime, and the
artifact carries it.

## Exercises

1. Print the longest line length as a fourth number.
2. Count lines without a trailing newline correctly (the last line still counts).
3. Make it read a file named by `argv[1]` instead of stdin. (Hint: `_start` has no arguments;
   read `/proc/self/cmdline` — the project's own tools do exactly that.)
