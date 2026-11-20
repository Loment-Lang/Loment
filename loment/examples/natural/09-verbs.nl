program verbs

note 三个动词都用上：`paint` 写内存、`say` 写文本/数、`talk to the machine` 发系统调用。

to _start
    let buf be a buffer of 2 bytes
    paint 65 at 0 in buf
    paint 10 at 1 in buf
    say "verbs: "
    say the number load8 of buf, 0
    say " "
    talk to the machine 1 with 1, buf, 2
    talk to the machine 60 with 0, 0, 0
end
