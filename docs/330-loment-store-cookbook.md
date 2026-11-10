# store cookbook：按任务找库

> 配套 `docs/store-index.md`（全部模块的公开名字）。这里只回答"做这件事，用哪几个
> 函数、什么顺序"。所有例子都是 Loment（表层语法），路径一律是 **NUL 结尾的 C 串**
> —— 库函数不做隐式转换，用 `fu_cstr` / `k32_cstr` 把字符串拷进缓冲区。

## 1 读一个文件全部内容

```rust
use fsops

fn main() -> u32 {
    let path: ptr = alloc(256);
    let _n: u32 = sys_cstr(path, 256, "/etc/hostname");
    let buf: ptr = alloc(4096);
    let got: i64 = fp_read_all(path, buf, 4096);
    if got < 0 {
        return 1;
    }
    let _w: i64 = syscall4(1, 1, buf as u64, got as u64);
    return 0;
}
```

`fp_read_all` 先看大小：装不下返回 `-28`（EMSGSIZE），**不会**给你半截内容。

## 2 原子写文件

```rust
let tmp: ptr = alloc(256);
let _x: u32 = sys_cstr(tmp, 256, "/tmp/out.json.new");
let rc: i64 = fp_write_atomic(path, tmp, data, n, 420 as u32);
```

顺序是 write → fsync → close → rename；失败会把临时文件删掉。要的就是"读者要么看到
旧文件，要么看到完整的新文件"。

## 3 遍历 / 删除 / 复制目录树

```rust
use fsops

let dent: ptr = alloc(8192);
let list: ptr = alloc(65536);
let n: i64 = fp_walk(root, dent, 8192, list, 65536);   // NUL 分隔的相对路径
```

- 遍历深度上限 8，删除/复制上限 32 层；越界返回 `-40`，装不下返回 `-28`。
- `fp_remove_tree(root, dent, 8192)` 不跟随符号链接。
- `fp_copy_tree(src, dst, buf, cap)` 把符号链接按**链接**重建，不复制目标内容。

## 4 监听目录变化

```rust
use watcher

let fd: i64 = wa2_init1(524288 as u32);   // IN_CLOEXEC
let wd: i64 = wa2_add(fd, dir, WA2_CREATE | WA2_CLOSE_WRITE);
let buf: ptr = alloc(4096);
let n: i64 = wa2_read(fd, buf, 4096);
let pos: u32 = 0;                         // 游标是普通变量，逐条前进
while pos < (n as u32) {
    let ev: ptr = ((buf as u64) + (pos as u64)) as ptr;
    let mask: u32 = wa2_ev_mask(ev);
    let name: ptr = wa2_ev_name(ev);
    // 处理事件……
    pos = pos + wa2_event_size(wa2_ev_len(ev));
}
```

事件按 16 字节对齐；名字长度是 `wa2_ev_len`（含结尾 NUL）。

## 5 一个事件循环（epoll + 定时器）

```rust
use eventloop

let sc: ptr = alloc(el_state_bytes());
if el_open(sc) < 0 { return 1; }
let _a: i64 = el_add_fd(sc, fd, EL_READ, 1 as u64);        // tag 1 = 这个 fd
let _t: i64 = el_add_timer(sc, 1000 as u64, 1000 as u64, 9 as u64);  // 每秒
let evs: ptr = alloc(16 * 16);
let epb: ptr = alloc(65536);
let running: u32 = 1;
while running == 1 {
    let n: i64 = el_wait(sc, evs, 16, 1000 as i64, epb);
    let i: u32 = 0;
    while i < (n as u32) {
        let ev: ptr = ((evs as u64) + ((i as u64) * 16 as u64)) as ptr;
        let tag: u32 = el_ev_tag(ev);
        if tag == 1 { /* 读那个 fd */ }
        if tag == 9 { let ticks: u64 = el_take_timer(sc); }
        i = i + 1;
    }
}
el_close(sc);
```

没有回调：循环把事件交回来，`tag` 决定你走哪条分支。`el_wake(sc)` 能让阻塞中的
`el_wait` 立刻返回（另一个线程或信号处理里用）。

## 6 unix domain socket

```rust
use unixsock

let lfd: i64 = ux_listen_path(sock_path, 16 as u64);   // 结束后自己 ux_unlink
let cfd: i64 = ux_accept(lfd);
let _r: i64 = ux_recv(cfd, buf, 4096 as u64, 0 as u64);
```

- 抽象命名空间：路径字符串以 `@` 开头，`ux_addr` 会翻译成首字节 NUL。
- 传 fd：`ux_send_fd(fd, data, n, pass_fd, scratch)` / `ux_recv_fd(fd, buf, cap, scratch)`，
  `scratch` 是调用方给的 96 字节。

## 7 一个最小 HTTP 服务

```rust
use httpsrv

let routes: str = "GET /health health\nGET / h2\n* /api api";
let rbuf: ptr = alloc(64);
let rn: u32 = sys_cstr(rbuf, 64, routes);
let lfd: i64 = hs_listen(0 as u32, 8080 as u32, 64 as u64, sa, on);
while running == 1 {
    let cfd: i64 = hs_accept(lfd);
    let req: ptr = alloc(8192);
    let n: i64 = hs_read_request(cfd, req, 8192);
    let path: ptr = alloc(256);
    let pn: u32 = hs_path(req, n as u32, path, 256);
    let id: i64 = hs_route(rbuf, rn, "GET", path, pn);
    let hb: ptr = alloc(512);
    if id == 0 {
        let _x: i64 = hs_reply_text(cfd, 200 as u32, "ok\n", hb, 512);
    } else {
        let _x: i64 = hs_reply_text(cfd, 404 as u32, "not found\n", hb, 512);
    }
}
```

`hs_read_request` 只保证读到请求头；正文长度用 `hs_body_len` 再读。路由表是纯文本，
`id` 默认是行号。

## 8 TLS 1.3 的骨架（**没有互通承诺**）

```rust
use tls
use x25519

let priv: ptr = alloc(32);
let pubk: ptr = alloc(32);
let sc: ptr = alloc(x25519_scratch());
// 用 getrandom 填 priv，再 x25519_base(priv, pubk, sc)
let ch: ptr = alloc(2048);
let n: i64 = tls_client_hello(ch, 2048, host, hn, pubk, rnd, tsc);
```

服务端回来后：`tls_parse_server_hello` 取随机数与对端公钥 → `x25519` 得共享密钥 →
`tls_expand_label` 走完整密钥表 → `tls_rec_open` 解记录。**只支持
`TLS_CHACHA20_POLY1305_SHA256` + x25519 + Ed25519 叶证书**；RSA/ECDSA 证书、会话票据、
0-RTT 都不在范围内，且**没有**与真服务器互通的行为判据（见 docs/328 §1）。

## 9 channel + 用户自己的 worker

`thread_spawn` 要**编译期的函数名**，所以没有通用线程池。能给的是一条消息通道和一条
任务队列，worker 自己写：

```rust
use channel

fn worker(ch: ptr) {
    let buf: ptr = alloc(4096);
    while running == 1 {
        let n: i64 = ch_recv(ch, buf, 4096, 0 as i64 - 1 as i64);
        if n < 0 { return; }
        // 处理 buf[0..n]
    }
}
```

`ch_send / ch_recv` 满/空时用进程私有 futex 等待；`timeout_ms < 0` 表示一直等。

## 10 任务队列

```rust
use jobqueue

let q: ptr = alloc(jq_bytes(64));
let _i: u32 = jq_init(q, 64 as u32);
let _p: i64 = jq_push(q, job_id, 0 as i64);
let out: ptr = alloc(8);
let _g: i64 = jq_pop(q, out, 0 as i64 - 1 as i64);
```

worker 是用户写的 `thread_spawn` 入口；队列只负责阻塞与顺序。

## 11 Windows：读一个文件（PE 构建）

```rust
use kernel32

let path: ptr = alloc(512);
let _n: u32 = k32_cstr("C:\\boot.ini", path, 512);
let h: u64 = k32_open_read(path);
if k32_invalid(h) { return 1; }
let buf: ptr = alloc(4096);
let got: i64 = k32_read(h, buf, 4096);
let _c: bool = k32_close(h);
```

**PE 的 FFI 还没开**（#1262）：这层今天是声明的正确形状（签名、常数、结构体布局），
链接到 Kernel32 要等那条打开。

## 12 定时（timerfd）

```rust
use interval

let fd: i64 = iv_create(IV_CLOEXEC);
let _a: i64 = iv_arm(fd, 100 as u64, 1000 as u64);   // 100ms 后首次，之后每秒
let ticks: i64 = iv_wait(fd);                        // 读到的是"到期次数"
```

`iv_wait` 返回的是自上次读以来到期的次数 —— 慢的消费者会拿到大于 1 的值，这是
timerfd 的语义，不是丢事件。
