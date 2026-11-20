program generics

note 泛型：`for any T` 之后，类型那一格就能写 T。

to largest for any T with a as a T and b as a T giving a T
    when a is above b
        give back a
    end
    give back b
end

to _start
    let eight be 8
    let three be 3
    let big be (largest of eight, three) as a whole number
    say the number big
    say "\n"
    talk to the machine 60 with 0, 0, 0
end
