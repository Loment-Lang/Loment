package com.example;

// StringBuilder from the runtime: append(String/int/long/char) and toString().
public class SbLib {
    public static int run() {
        StringBuilder sb = new StringBuilder();
        sb.append("n=");
        sb.append(7);
        sb.append(',');
        sb.append(-3L);
        sb.append('x');
        String s = sb.toString();
        System.out.println(s);
        return s.length();
    }
}
