//: `System.out.print` / `println` 与 `String` —— **输出这一路**的语料。
//
// 这一份钉的是"写出去的字节"：`entry()` 打印几行并返回 7，两条链比的是
// **stdout 的原始字节**与退出码（`test_printing_matches_javac_stdout`）。
// 任何一处翻错都会让两串字节不同：
//
//   * `println` 少写一个换行 —— 第一行与第二行会粘在一起；
//   * 整数走了 `print`（而不是 `print_num`）—— 打不出数字；
//   * `String` 拼接变成空串 —— 第三行少一个 `ab`；
//   * `print` 后面被自动补了换行 —— `no newline` 那行会多一个空行。

public class Print {
    public static int entry() {
        System.out.println("hello, world");
        int x = 6 * 7;
        System.out.println(x);
        String s = "a" + "b";
        System.out.println(s);
        System.out.print("no newline");
        System.out.println();
        System.out.println("tab\there");
        if (x > 40) {
            System.out.println("big");
        }
        return 7;
    }
}
