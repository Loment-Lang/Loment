program iflet

note 看形状：**一条臂、没有兜底** -> `if let`。

a Kind is either Small or Big carrying a whole number

to pick with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w
    end
    give back 0
end

to _start
    let k be a Kind that is Small
    let b be a Kind that is Big carrying 9
    say the number pick of k plus pick of b
    say "\n"
    talk to the machine 60 with 0, 0, 0
end
