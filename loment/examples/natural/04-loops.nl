program loops

note `let` / `set`、`for i from 1 to n`（`to` 不含终点）、`while … is above …`。

to total with n as a whole number giving a whole number
    let acc be 0
    for i from 1 to n
        set acc to acc plus i
    end
    let v be n
    while v is above 0
        set acc to acc plus v
        set v to v minus 1
    end
    give back acc
end

to _start
    say the number total of 4
    say "\n"
    talk to the machine 60 with 0, 0, 0
end
