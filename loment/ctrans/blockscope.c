/* blockscope.c —— 第六份语料：**块作用域**。
 *
 * 本语言**没有块作用域**（`docs/188` §0.1.1 实测：块里 `let` 的名块外看得见），
 * 所以 `if` / `while` / `for` 的体里声明的名不能靠 `{ }` 圈住 —— 要么**改名**
 * （遮蔽外层时），要么出块时**撤掉**（这才是体里新声明的名）。这一份把三条都钉住：
 *
 *   if_shadow     `if` 体里遮蔽外层的 `x` —— 出块后外层那个 `x` 必须还是 1
 *   for_shadow    `for` 体里声明一个**与循环变量同名**的 `i` —— 遮蔽了的话步进会写到
 *                 错的那个名上，`i` 永不前进 ⇒ **死循环**（GCC 27，译文原先不终止）
 *   while_shadow  `while` 体里遮蔽外层的 `x`
 *   redecl_ok     跨块**重新声明**同一个名（`if` 体里一个 `y`、函数层又一个）——
 *                 合法，别误报"重复声明"
 *
 * main 按位权凑成一个数当退出码（期望值在判据里照着 C 的语义手推）。
 */
int if_shadow(int c) {
    int x = 1;
    if (c) {
        int x = 2;
        x = x + 1;
    }
    return x;                       /* C：1（体里那个 x 出了块就没了） */
}

int for_shadow(void) {
    int s = 0;
    for (int i = 0; i < 3; i = i + 1) {
        int i = 9;
        s = s + i;
    }
    return s;                       /* C：27（步进写的是**循环变量**） */
}

int while_shadow(void) {
    int x = 1;
    int i = 0;
    while (i < 2) {
        int x = 5;
        x = x + 1;
        i = i + 1;
    }
    return x;                       /* C：1（改的也是体里那个 x） */
}

int redecl_ok(int c) {
    if (c) {
        int y = 1;
    }
    int y = 2;
    return y;                       /* C：2（两处作用域不同，合法） */
}

int main() {
    int s = 0;
    s = s + if_shadow(1) * 1;
    s = s + (for_shadow() == 27) * 2;
    s = s + while_shadow() * 4;
    s = s + redecl_ok(1) * 8;
    return s % 256;
}
