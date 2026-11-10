# prints.py —— **输出这一路**的语料（`loment_pytrans_test` 的 stdout 对拍）。
#
# 钉的是"写出去的字节"：`main()` 打印几行并返回 7，CPython 与译文两条链比
# **stdout 的原始字节**与退出码。任何一处翻错都会让两串字节不同：
#
#   * `print` 少写换行 —— 第一行与第二行会粘在一起；
#   * 整数走了 `print`（而不是 `print_num`）—— 打不出数字；
#   * 拼接变成空串 —— 第三行少一个 `ab`；
#   * 转义 `\t` / `\"` 没翻对 —— 第四、五行字节不同。


def main() -> int:
    print("hello, world")
    n = 6 * 7
    print(n)
    s = "a" + "b"
    print(s)
    print("tab\there")
    print("say \"hi\"")
    parity = "even" if n % 2 == 0 else "odd"   # 条件表达式（赋值右边）
    print(parity)
    print()
    return 7
