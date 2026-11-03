/*
 * loment-restart — LMS 第 6 级（`docs/253` §5.5，issue #390）。
 *
 * 一个**用 C 写的**重启器：跑一个程序；它死了就把它拉起来。
 *
 * 为什么是 C，而不是 Loment（三条，第二条是全部理由）：
 *
 *   1. C 在本仓已经是**另一条实现线**（`tools/lctrans.py` 那门表层语法），所以这不是
 *      引入一个新东西；
 *   2. **它必须不依赖它要重启的那个东西。** 一个用 Loment 写的重启器，在 Loment 崩了
 *      之后还能不能跑，是**它自己要回答的问题** —— 循环。写成 C 就把这个循环切断；
 *   3. 前史：MINIX 3 的 **reincarnation server**（Herder 等，EuroSys'06 / DSN'07）——
 *      驱动是用户态进程，被 kill 之后由 RS 起回来。
 *
 * ⚠ **它不是信任地板，只是一半。** 准确地说是「**重启器 + 最小子集 + 编它们的那些
 * 工具链**」—— 单独一个 C 重启器回答不了 Thompson 那道题（编它的那个 C 编译器又怎么信）。
 * 这一句必须跟着这个文件走，否则它就是一块**没被标注的地板**（`docs/253` §1.2）。
 *
 * 语义，**全部写死在这里**：
 *   * 退出码 0 ⇒ 不重启，原样返回 0。正常结束不是崩溃。
 *   * 非 0 退出 / 被信号杀死 ⇒ 重启，并往 stderr 写一行 `restart <n> after <status>`。
 *   * **最多重启 `LOMENT_RESTART_MAX` 次**（默认 5）。崩到第 6 次就**停**并把最后一次的
 *     状态返回 —— 一个永远重启的守护进程会把一次真崩溃变成一条看不见的日志。
 *   * 每次重启之间睡 `LOMENT_RESTART_DELAY_MS`（默认 200 ms），让"启动即崩"不至于烧满 CPU。
 *   * **彩蛋**：设了 `LOMENT_RESTART_KEY` 才核对重启密钥。三个密钥走三种不同的摘要
 *     （SHA-256 / SHA-1 / MD5），源码里只有**盐和摘要**，没有明文；不设就是原行为，
 *     一点不变。对上了照常重启，只在 stderr 说一句；对不上就拒绝重启，把程序最后的
 *     状态原样返回。
 *   * `--selftest` 跑三个算法的已知答案（给判据用，不进重启主循环）。
 *
 * 不做的事（明写，免得被读成承诺）：不做进程组、不做多进程、不做状态检查点 ——
 * "退出所有 Loment 进程"在 `no_std` 下**表达不出来**（`docs/253` §5.5 那条硬边界），
 * 这里能做到的是**自己退出、让 OS 收尸、由父进程拉起**。
 *
 * 编译: cc -O2 -o loment-restart loment-restart.c
 * 用法: ./loment-restart <program> [args...]
 *       LOMENT_RESTART_KEY=<key> ./loment-restart <program> [args...]
 *       ./loment-restart --selftest
 */

#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef LOMENT_RESTART_MAX
#define LOMENT_RESTART_MAX 5
#endif

#ifndef LOMENT_RESTART_DELAY_MS
#define LOMENT_RESTART_DELAY_MS 200
#endif

/* ── 彩蛋：三个摘要算法，各一份自带实现 ──────────────────────────────
 *
 * 重启器不许依赖它要重启的东西，也不该为了一个彩蛋去拉 OpenSSL。所以这里
 * 自带 SHA-256 / SHA-1 / MD5 各一份。它们的用途只有一件事：把
 * `LOMENT_RESTART_KEY` 与每个密钥各自的盐拼起来，和源码里存的摘要比。
 * 源码里**不存明文**，只存盐与摘要。
 */

static uint32_t rotr32(uint32_t x, unsigned n) {
    return (x >> n) | (x << (32 - n));
}

static uint32_t rol32(uint32_t x, unsigned n) {
    return (x << n) | (x >> (32 - n));
}

/* ── SHA-256 ─────────────────────────────────────────────────────── */

static const uint32_t K256[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
    0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
    0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
    0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

typedef struct {
    uint32_t h[8];
    uint64_t bits;
    unsigned char buf[64];
    size_t used;
} Sha256;

static void sha256_block(Sha256 *s, const unsigned char *p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) |
               ((uint32_t)p[4 * i + 2] << 8) | (uint32_t)p[4 * i + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3];
    uint32_t e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t s1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        uint32_t ch = (e & f) ^ (~e & g);
        uint32_t t1 = h + s1 + ch + K256[i] + w[i];
        uint32_t s0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d;
    s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha256_init(Sha256 *s) {
    s->h[0] = 0x6a09e667u; s->h[1] = 0xbb67ae85u;
    s->h[2] = 0x3c6ef372u; s->h[3] = 0xa54ff53au;
    s->h[4] = 0x510e527fu; s->h[5] = 0x9b05688cu;
    s->h[6] = 0x1f83d9abu; s->h[7] = 0x5be0cd19u;
    s->bits = 0;
    s->used = 0;
}

static void sha256_update(Sha256 *s, const unsigned char *p, size_t n) {
    s->bits += (uint64_t)n * 8;
    while (n > 0) {
        size_t room = sizeof s->buf - s->used;
        size_t take = n < room ? n : room;
        memcpy(s->buf + s->used, p, take);
        s->used += take;
        p += take;
        n -= take;
        if (s->used == sizeof s->buf) {
            sha256_block(s, s->buf);
            s->used = 0;
        }
    }
}

static void sha256_final(Sha256 *s, unsigned char out[32]) {
    uint64_t bits = s->bits;
    unsigned char pad[72];
    size_t padlen = s->used < 56 ? 56 - s->used : 120 - s->used;
    memset(pad, 0, sizeof pad);
    pad[0] = 0x80;
    for (int i = 0; i < 8; i++) {
        pad[padlen + i] = (unsigned char)(bits >> (56 - 8 * i));
    }
    sha256_update(s, pad, padlen + 8);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (unsigned char)(s->h[i] >> 24);
        out[4 * i + 1] = (unsigned char)(s->h[i] >> 16);
        out[4 * i + 2] = (unsigned char)(s->h[i] >> 8);
        out[4 * i + 3] = (unsigned char)s->h[i];
    }
}

static void sha256(const unsigned char *p, size_t n, unsigned char *out) {
    Sha256 s;
    sha256_init(&s);
    sha256_update(&s, p, n);
    sha256_final(&s, out);
}

/* ── SHA-1 ───────────────────────────────────────────────────────── */

typedef struct {
    uint32_t h[5];
    uint64_t bits;
    unsigned char buf[64];
    size_t used;
} Sha1;

static void sha1_block(Sha1 *s, const unsigned char *p) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) |
               ((uint32_t)p[4 * i + 2] << 8) | (uint32_t)p[4 * i + 3];
    }
    for (int i = 16; i < 80; i++) {
        w[i] = rol32(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    uint32_t a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) {
            f = (b & c) | (~b & d); k = 0x5a827999u;
        } else if (i < 40) {
            f = b ^ c ^ d; k = 0x6ed9eba1u;
        } else if (i < 60) {
            f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdcu;
        } else {
            f = b ^ c ^ d; k = 0xca62c1d6u;
        }
        uint32_t t = rol32(a, 5) + f + e + k + w[i];
        e = d; d = c; c = rol32(b, 30); b = a; a = t;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e;
}

static void sha1_init(Sha1 *s) {
    s->h[0] = 0x67452301u; s->h[1] = 0xefcdab89u;
    s->h[2] = 0x98badcfeu; s->h[3] = 0x10325476u;
    s->h[4] = 0xc3d2e1f0u;
    s->bits = 0;
    s->used = 0;
}

static void sha1_update(Sha1 *s, const unsigned char *p, size_t n) {
    s->bits += (uint64_t)n * 8;
    while (n > 0) {
        size_t room = sizeof s->buf - s->used;
        size_t take = n < room ? n : room;
        memcpy(s->buf + s->used, p, take);
        s->used += take;
        p += take;
        n -= take;
        if (s->used == sizeof s->buf) {
            sha1_block(s, s->buf);
            s->used = 0;
        }
    }
}

static void sha1_final(Sha1 *s, unsigned char out[20]) {
    uint64_t bits = s->bits;
    unsigned char pad[72];
    size_t padlen = s->used < 56 ? 56 - s->used : 120 - s->used;
    memset(pad, 0, sizeof pad);
    pad[0] = 0x80;
    for (int i = 0; i < 8; i++) {
        pad[padlen + i] = (unsigned char)(bits >> (56 - 8 * i));
    }
    sha1_update(s, pad, padlen + 8);
    for (int i = 0; i < 5; i++) {
        out[4 * i] = (unsigned char)(s->h[i] >> 24);
        out[4 * i + 1] = (unsigned char)(s->h[i] >> 16);
        out[4 * i + 2] = (unsigned char)(s->h[i] >> 8);
        out[4 * i + 3] = (unsigned char)s->h[i];
    }
}

static void sha1(const unsigned char *p, size_t n, unsigned char *out) {
    Sha1 s;
    sha1_init(&s);
    sha1_update(&s, p, n);
    sha1_final(&s, out);
}

/* ── MD5 ─────────────────────────────────────────────────────────── */

static const uint32_t KMD5[64] = {
    0xd76aa478u, 0xe8c7b756u, 0x242070dbu, 0xc1bdceeeu,
    0xf57c0fafu, 0x4787c62au, 0xa8304613u, 0xfd469501u,
    0x698098d8u, 0x8b44f7afu, 0xffff5bb1u, 0x895cd7beu,
    0x6b901122u, 0xfd987193u, 0xa679438eu, 0x49b40821u,
    0xf61e2562u, 0xc040b340u, 0x265e5a51u, 0xe9b6c7aau,
    0xd62f105du, 0x02441453u, 0xd8a1e681u, 0xe7d3fbc8u,
    0x21e1cde6u, 0xc33707d6u, 0xf4d50d87u, 0x455a14edu,
    0xa9e3e905u, 0xfcefa3f8u, 0x676f02d9u, 0x8d2a4c8au,
    0xfffa3942u, 0x8771f681u, 0x6d9d6122u, 0xfde5380cu,
    0xa4beea44u, 0x4bdecfa9u, 0xf6bb4b60u, 0xbebfbc70u,
    0x289b7ec6u, 0xeaa127fau, 0xd4ef3085u, 0x04881d05u,
    0xd9d4d039u, 0xe6db99e5u, 0x1fa27cf8u, 0xc4ac5665u,
    0xf4292244u, 0x432aff97u, 0xab9423a7u, 0xfc93a039u,
    0x655b59c3u, 0x8f0ccc92u, 0xffeff47du, 0x85845dd1u,
    0x6fa87e4fu, 0xfe2ce6e0u, 0xa3014314u, 0x4e0811a1u,
    0xf7537e82u, 0xbd3af235u, 0x2ad7d2bbu, 0xeb86d391u,
};

static const unsigned char SMD5[64] = {
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
};

typedef struct {
    uint32_t h[4];
    uint64_t bits;
    unsigned char buf[64];
    size_t used;
} Md5;

static void md5_block(Md5 *s, const unsigned char *p) {
    uint32_t m[16];
    for (int i = 0; i < 16; i++) {
        m[i] = (uint32_t)p[4 * i] | ((uint32_t)p[4 * i + 1] << 8) |
               ((uint32_t)p[4 * i + 2] << 16) | ((uint32_t)p[4 * i + 3] << 24);
    }
    uint32_t a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3];
    for (int i = 0; i < 64; i++) {
        uint32_t f;
        int g;
        if (i < 16) {
            f = (b & c) | (~b & d); g = i;
        } else if (i < 32) {
            f = (d & b) | (~d & c); g = (5 * i + 1) % 16;
        } else if (i < 48) {
            f = b ^ c ^ d; g = (3 * i + 5) % 16;
        } else {
            f = c ^ (b | ~d); g = (7 * i) % 16;
        }
        uint32_t tmp = d;
        d = c;
        c = b;
        b = b + rol32(a + f + KMD5[i] + m[g], SMD5[i]);
        a = tmp;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d;
}

static void md5_init(Md5 *s) {
    s->h[0] = 0x67452301u; s->h[1] = 0xefcdab89u;
    s->h[2] = 0x98badcfeu; s->h[3] = 0x10325476u;
    s->bits = 0;
    s->used = 0;
}

static void md5_update(Md5 *s, const unsigned char *p, size_t n) {
    s->bits += (uint64_t)n * 8;
    while (n > 0) {
        size_t room = sizeof s->buf - s->used;
        size_t take = n < room ? n : room;
        memcpy(s->buf + s->used, p, take);
        s->used += take;
        p += take;
        n -= take;
        if (s->used == sizeof s->buf) {
            md5_block(s, s->buf);
            s->used = 0;
        }
    }
}

static void md5_final(Md5 *s, unsigned char out[16]) {
    uint64_t bits = s->bits;
    unsigned char pad[72];
    size_t padlen = s->used < 56 ? 56 - s->used : 120 - s->used;
    memset(pad, 0, sizeof pad);
    pad[0] = 0x80;
    for (int i = 0; i < 8; i++) {
        pad[padlen + i] = (unsigned char)(bits >> (8 * i));
    }
    md5_update(s, pad, padlen + 8);
    for (int i = 0; i < 4; i++) {
        out[4 * i] = (unsigned char)s->h[i];
        out[4 * i + 1] = (unsigned char)(s->h[i] >> 8);
        out[4 * i + 2] = (unsigned char)(s->h[i] >> 16);
        out[4 * i + 3] = (unsigned char)(s->h[i] >> 24);
    }
}

static void md5(const unsigned char *p, size_t n, unsigned char *out) {
    Md5 s;
    md5_init(&s);
    md5_update(&s, p, n);
    md5_final(&s, out);
}

/* ── 彩蛋的密钥表：只有盐和摘要，没有明文 ───────────────────────── */

typedef void (*digest_fn)(const unsigned char *data, size_t len, unsigned char *out);

typedef struct {
    const char *name;
    digest_fn fn;
    size_t digest_len;
    const char *salt;
    const char *digest_hex;
    const char *note;
} RestartKey;

static const RestartKey RESTART_KEYS[] = {
    {
        "sha256", sha256, 32, "comp.os.minix",
        "37ae4c15cf75184d39f9c630c7dba9255a0dad076d79db8add664526bae3e250",
        "hello, everybody out there using minix (1991-08-25)",
    },
    {
        "sha1", sha1, 20, "just-for-fun",
        "256db84348e467d419694c5ce3e6f2a96bfd771d",
        "just for fun",
    },
    {
        "md5", md5, 16, "open.source",
        "d22aed05a8f3ceaebadc2ad0624a18d7",
        "hi, open source",
    },
};

#define RESTART_KEY_MAX 256

static void to_hex(const unsigned char *d, size_t n, char *out) {
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[2 * i] = hex[d[i] >> 4];
        out[2 * i + 1] = hex[d[i] & 15];
    }
    out[2 * n] = '\0';
}

/* 定长比较：不因为第一个不同字节就提前返回。彩蛋不走侧信道，但也不白送。 */
static int ct_eq(const char *a, const char *b, size_t n) {
    unsigned char diff = 0;
    for (size_t i = 0; i < n; i++) {
        diff |= (unsigned char)(a[i] ^ b[i]);
    }
    return diff == 0;
}

/* 返回命中的下标；-1 = 不对。 */
static int restart_key_matches(const char *key) {
    size_t klen = strlen(key);
    char buf[RESTART_KEY_MAX + 64];
    unsigned char dig[32];
    char hex[65];
    if (klen > RESTART_KEY_MAX) {
        return -1;
    }
    for (size_t i = 0; i < sizeof RESTART_KEYS / sizeof RESTART_KEYS[0]; i++) {
        const RestartKey *k = &RESTART_KEYS[i];
        size_t slen = strlen(k->salt);
        memcpy(buf, k->salt, slen);
        memcpy(buf + slen, key, klen);
        memset(dig, 0, sizeof dig);
        k->fn((const unsigned char *)buf, slen + klen, dig);
        to_hex(dig, k->digest_len, hex);
        if (strlen(k->digest_hex) == k->digest_len * 2 &&
            ct_eq(hex, k->digest_hex, k->digest_len * 2)) {
            return (int)i;
        }
    }
    return -1;
}

/* 1 = 允许重启（没设密钥也算）；0 = 密钥不对，拒绝。 */
static int restart_key_ok(int last_status) {
    const char *key = getenv("LOMENT_RESTART_KEY");
    static int announced = 0;
    int which;
    if (key == NULL || key[0] == '\0') {
        return 1;
    }
    which = restart_key_matches(key);
    if (which >= 0) {
        if (!announced) {
            fprintf(stderr, "loment-restart: restart key accepted (%s) — %s\n",
                    RESTART_KEYS[which].name, RESTART_KEYS[which].note);
            announced = 1;
        }
        return 1;
    }
    fprintf(stderr, "loment-restart: restart key rejected; refusing to restart "
                    "(last status %d)\n", last_status);
    return 0;
}

/* 三个算法的已知答案；`--selftest` 跑它。最后一条是 100 字节，跨过块边界。 */
static int hash_selftest(void) {
    char longmsg[100];
    memset(longmsg, 'a', sizeof longmsg);
    typedef struct {
        const char *name;
        digest_fn fn;
        size_t digest_len;
        const char *msg;
        size_t len;
        const char *want;
    } Kat;
    Kat cases[] = {
        {"sha256", sha256, 32, "", 0,
         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        {"sha256", sha256, 32, "abc", 3,
         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        {"sha256", sha256, 32, longmsg, 100,
         "2816597888e4a0d3a36b82b83316ab32680eb8f00f8cd3b904d681246d285a0e"},
        {"sha1", sha1, 20, "", 0,
         "da39a3ee5e6b4b0d3255bfef95601890afd80709"},
        {"sha1", sha1, 20, "abc", 3,
         "a9993e364706816aba3e25717850c26c9cd0d89d"},
        {"sha1", sha1, 20, longmsg, 100,
         "7f9000257a4918d7072655ea468540cdcbd42e0c"},
        {"md5", md5, 16, "", 0,
         "d41d8cd98f00b204e9800998ecf8427e"},
        {"md5", md5, 16, "abc", 3,
         "900150983cd24fb0d6963f7d28e17f72"},
        {"md5", md5, 16, longmsg, 100,
         "36a92cc94a9e0fa21f625f8bfb007adf"},
    };
    int bad = 0;
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        unsigned char raw[32];
        char hex[65];
        memset(raw, 0, sizeof raw);
        cases[i].fn((const unsigned char *)cases[i].msg, cases[i].len, raw);
        to_hex(raw, cases[i].digest_len, hex);
        printf("%s %zu %s\n", cases[i].name, cases[i].len, hex);
        if (strcmp(hex, cases[i].want) != 0) {
            fprintf(stderr, "loment-restart: selftest %s(len %zu): %s != %s\n",
                    cases[i].name, cases[i].len, hex, cases[i].want);
            bad = 1;
        }
    }
    if (bad) {
        fprintf(stderr, "loment-restart: selftest failed\n");
    }
    return bad;
}

static void nap_ms(long ms) {
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {
        /* 被信号打断就把剩下的睡完 —— 这里不丢唤醒。 */
    }
}

/* 跑一次，返回 (退出码 | 128+信号)；起不来返回 127。 */
static int run_once(char **argv) {
    pid_t pid = fork();
    if (pid < 0) {
        fprintf(stderr, "loment-restart: fork: %s\n", strerror(errno));
        return 127;
    }
    if (pid == 0) {
        execvp(argv[0], argv);
        fprintf(stderr, "loment-restart: exec %s: %s\n", argv[0], strerror(errno));
        _exit(127);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) {
            fprintf(stderr, "loment-restart: waitpid: %s\n", strerror(errno));
            return 127;
        }
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    return 127;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--selftest") == 0) {
        return hash_selftest();
    }
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program> [args...]\n", argv[0]);
        return 2;
    }
    int restarts = 0;
    for (;;) {
        int rc = run_once(&argv[1]);
        if (rc == 0) {
            return 0;
        }
        if (!restart_key_ok(rc)) {
            return rc;
        }
        if (restarts >= LOMENT_RESTART_MAX) {
            fprintf(stderr, "loment-restart: giving up after %d restarts (last status %d)\n",
                    restarts, rc);
            return rc;
        }
        restarts += 1;
        fprintf(stderr, "loment-restart: restart %d after %d\n", restarts, rc);
        fflush(stderr);
        nap_ms(LOMENT_RESTART_DELAY_MS);
    }
}
