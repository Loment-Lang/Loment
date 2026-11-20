# script.py —— **脚本式写法**：没有 `def main`，顶层语句就是程序。
#
# 这条规则只对 `choose write grammar python` 成立（用户 2026-11-10 的裁定）：
# 顶层可执行语句合成入口，别的表层语法与原生 Loment 一行不动。
# `loment_pytrans_test::test_script_style_matches_cpython` 拿 CPython 直接跑这份文件
# 当对照，比 stdout 的原始字节。

print("script mode")
n = 6 * 7
print(n)
s = "a" + "b"
print(s)
