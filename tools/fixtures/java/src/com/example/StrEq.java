package com.example;

/** String.equals / hashCode over ASCII and non-ASCII (docs/281 §7). */
public class StrEq {
    public static int run() {
        String a = "abc";
        String b = "abc";
        String c = "abd";
        String u = "中文";
        int r = 0;
        if (a.equals(b)) { r = r + 1; }
        if (!a.equals(c)) { r = r + 2; }
        if (!a.equals("ab")) { r = r + 4; }
        if (a.hashCode() == 96354) { r = r + 8; }
        if (u.hashCode() == 646394) { r = r + 16; }
        if (u.equals("中文")) { r = r + 32; }
        return r;
    }
}
