# A real program: wc in Loment

Every language book needs a program with input, output and a loop. This one reads standard input
and prints three numbers: lines, words and bytes — a `wc -l -w -c` in miniature.

## The whole program

```rust,run
module wc

choose runtime

def is_space(c: u32) -> bool {
    return c == 32 or c == 9 or c == 10 or c == 13;
}

fn main() {
    let buf: ptr = alloc(4096);
    let lines: u32 = 0;
    let words: u32 = 0;
    let bytes: u64 = 0;
    let inword: u32 = 0;
    while true {
        let n: i64 = syscall4(0, 0, buf as u64, 4096);
        if n <= 0 {
            break;
        }
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
    print_num(lines);
    print(" ");
    print_num(words);
    print(" ");
    print_num(bytes);
    print("\n");
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

**The loop leaves with `break`.** `while true` reads until the kernel returns zero or a negative
value, and `break` leaves the innermost loop. Tur 2026.2 added `break` and `continue`; in a
Tur 2026.1 file you would use a flag and a condition instead. Both spellings compile, and chapter
20 covers the difference.

**Read in chunks.** `alloc(4096)` hands out a small buffer and the loop fills it again for every
block of input; the counters live across iterations. The heap is a fixed size, so reading a whole
file into memory is rarely the right shape here. Streaming is.

**Pointers are arithmetic.** `(buf as u64) + off` converts the pointer, adds, and passes the
result to the kernel. There is no pointer-typed `+`; conversion is explicit.

**Bytes are `load8`.** A file is bytes. `load8(buf, i)` reads one, and comparing it to `10` (LF)
counts a line. `is_space` treats space, tab, LF and CR as separators, the same four bytes `wc`
uses. `inword` carries the word state from one chunk to the next, so a word split across a chunk
boundary still counts once.

**Functions can come before or after their callers.** A name must exist somewhere in the unit;
the checker resolves it. There are no forward declarations to write.

**`choose runtime` is a declaration about the artifact.** This program uses `print_num`, which
lives in the runtime block. Without `choose runtime`, the compiler refuses the call and names the
construct (E031). The runtime block is also what carries `alloc` and the collector.

**`n <= 0` compares an `i64` with a literal.** The literal takes the width of the other side. When
both sides are variables, their widths must match; chapter 7 covers the rule.

## Reading the program as a pipeline

It helps to separate the program into four stages:

| Stage | What it does | Where it lives |
|---|---|---|
| input | read up to 4096 bytes | `syscall4(0, …)` |
| decode | turn bytes into a line/word decision | `load8`, `is_space` |
| accumulate | update three counters | the `while` body |
| report | print three integers | `print_num` |

Each stage is small, and the state between stages is explicit. That shape scales: a parser, a
network client, or a file indexer is the same four stages with different middle steps.

## Exercises

1. Print the longest line length as a fourth number.
2. Count a final line that has no trailing newline.
3. Read a file named on the command line instead of standard input. `main` takes no arguments;
   read `/proc/self/cmdline` (chapter 32 shows the pattern).
4. Replace `is_space` with a table lookup and measure the change. The compiler does not optimise
   the comparison chain for you.
5. Rewrite the loop with `continue` so the word branch sits at the top level.
