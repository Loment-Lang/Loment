program containers

note 容器形状：结构体 + `the <什么> of <东西>`、`a buffer of N bytes`。

a Point has x as a whole number and y as a whole number

to sum_point with p as a Point giving a whole number
    give back the x of p plus the y of p
end

to _start
    let p be a Point with x as 4 and y as 5
    let buf be a buffer of 2 bytes
    paint 7 at 0 in buf
    say the number sum_point of p plus load8 of buf, 0
    say "\n"
    talk to the machine 60 with 0, 0, 0
end
