program match

note 看形状：**两条臂** -> `match`（枚举 + `looks like`）。

a Kind is either Small or Big carrying a whole number

to score with k as a Kind giving a whole number
    when k looks like a Kind that is Big carrying w
        give back w times 2
    end
    when k looks like a Kind that is Small
        give back 1
    end
end

to _start
    let k be a Kind that is Big carrying 5
    say the number score of k
    say "\n"
    talk to the machine 60 with 0, 0, 0
end
