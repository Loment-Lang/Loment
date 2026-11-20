/* widths.c —— 第五份语料：**几种整数宽度混着用**。
 *
 * 前四份**全程只用 `int` 一个宽度**（`numeric.c` 的头部写着为什么 —— 那时候翻译器
 * 看不出 `u32` 与 `i32` 的区别）。这一份专挑那道缝：C 的**寻常算术转换**（窄的那侧
 * 提升到宽 / 无符号那侧）与三处**隐式整型转换**（返回 / 赋值 / 实参）。
 *
 * 每一段各钉一件事：
 *
 *   mixed_cmp     `int` 与 `unsigned` **比较** —— 不提升就按有符号算，答案相反
 *                 （`-1 > 0u`：C 把 `-1` 变成 4294967295 ⇒ 真；不转则假）
 *   mixed_add     `int` 与 `unsigned` **相加**（`-1 + 1u` 在 32 位里回绕成 0）
 *   long_cmp      `int` 与 `long` 相加后比较 —— 加法在 64 位做，比较也必须在 64 位
 *                 （原先加法 64 位、比较 32 位，`== 2147483648` 与 `< 0` **同时为真**）
 *   narrow_ret    `unsigned` 收进来、`int` 还出去（返回位置的隐式转换）
 *   widen_arg     `int` 实参传给收 `unsigned` 的函数（实参位置的隐式转换）
 *   sink_ok       `long` / `unsigned` 赋给 `int`（赋值位置的隐式转换）
 *
 * **字面量一律在 32 位以内** —— 超了本门**没有更宽的字面量写法**（解析层就截断，
 * 见 `_lit32`），所以那些大值全靠**算**出来（`0 - 1` 当 `unsigned` 就是 4294967295）。
 *
 * main 把这些的结论按位权凑成一个数当退出码（期望值在判据里**照着 C 的规则手推**，
 * 不抄 C 编出来的结果）。
 */
int mixed_cmp(void) {
    int a = 0 - 1;
    unsigned b = 0;
    return a > b;                       /* C：4294967295 > 0 ⇒ 1 */
}

int mixed_add(void) {
    int a = 0 - 1;
    unsigned b = 1;
    return (a + b) == 0;                /* C：-1 + 1u 回绕成 0 ⇒ 1 */
}

int long_cmp(void) {
    int x = 1;
    long y = 2147483647;
    return (x + y) > 0;                 /* C：2147483648 > 0 ⇒ 1 */
}

int narrow_ret(unsigned int v) {
    return v;                           /* u32 -> i32：4294967295 变 -1 */
}

int widen_arg(unsigned int a) {
    return a > 100;                     /* 收进来的 -1 已经是 4294967295 ⇒ 1 */
}

int sink_ok(void) {
    long a = 0 - 1;
    unsigned b = 5;
    int p = a;                          /* i64 -> i32：-1 还是 -1 */
    int q = b;                          /* u32 -> i32：5 还是 5 */
    return (p == (0 - 1)) + (q == 5);
}

int main() {
    int s = 0;
    s = s + mixed_cmp() * 1;
    s = s + mixed_add() * 2;
    s = s + long_cmp() * 4;
    s = s + (narrow_ret(0 - 1) == (0 - 1)) * 8;
    s = s + widen_arg(0 - 1) * 16;
    s = s + sink_ok() * 32;
    return s % 256;
}
