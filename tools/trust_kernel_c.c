/* trust_kernel_c.c — R4/R5 第二个独立内核 (docs/379 §3.4 / C6 / C11)
 *
 * 它检查同一份 `(artifact, derivation, conclusion)`，与 tools/trust_kernel.py
 * **不共享一行代码**：自带 JSON 解析、SHA-256、玩具源解析器、静态栈检查与解释器。
 *
 * 覆盖档位：core。带 `l0` / `l0_safe` 的推导（R2/R3 扩展）不在本内核的覆盖内，
 * 它返回 UNSUPPORTED（退出码 3），由商团按覆盖取票 —— 回避不是接受。
 *
 * 用法:
 *   trust_kernel_c verify --artifact A --derivation D [--conclusion C]
 *   trust_kernel_c rules
 *
 * 退出码: 0 = ACCEPT / 1 = REJECT / 2 = 读不了 / 3 = UNSUPPORTED。
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_DEPTH_JSON 64
#define MAX_NODES 4096
#define MAX_PREM 64
#define MAX_UNITS 64
#define MAX_INS 8192
#define MAX_MAP 8192
#define STACK_MAX 4096

/* ------------------------------------------------------------------ 错误出口 */

static void fail(const char *fmt, const char *a) {
    fprintf(stdout, "REJECT: ");
    fprintf(stdout, fmt, a ? a : "");
    fprintf(stdout, "\n");
    exit(1);
}

static void unsupported(const char *fmt) {
    fprintf(stdout, "UNSUPPORTED: %s\n", fmt);
    exit(3);
}

static void ioerr(const char *path) {
    fprintf(stderr, "ERR: cannot read %s\n", path);
    exit(2);
}

/* ------------------------------------------------------------------ 通用小工具 */

static void *xalloc(size_t n) {
    void *p = calloc(1, n ? n : 1);
    if (!p) {
        fprintf(stderr, "ERR: out of memory\n");
        exit(2);
    }
    return p;
}

static char *xstrndup(const char *p, size_t n) {
    char *s = (char *)xalloc(n + 1);
    memcpy(s, p, n);
    s[n] = '\0';
    return s;
}

static char *xstrdup(const char *p) { return xstrndup(p, strlen(p)); }

/* ------------------------------------------------------------------ SHA-256 */

typedef struct {
    uint32_t h[8];
    uint64_t len;
    unsigned char buf[64];
    size_t n;
} Sha;

static const uint32_t K256[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u,
    0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u,
    0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
    0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
    0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au,
    0x5b9cca4fu, 0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};

static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

static void sha_init(Sha *s) {
    static const uint32_t iv[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u,
                                   0xa54ff53au, 0x510e527fu, 0x9b05688cu,
                                   0x1f83d9abu, 0x5be0cd19u};
    memcpy(s->h, iv, sizeof iv);
    s->len = 0;
    s->n = 0;
}

static void sha_block(Sha *s, const unsigned char *p) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[4 * i] << 24) | ((uint32_t)p[4 * i + 1] << 16) |
               ((uint32_t)p[4 * i + 2] << 8) | (uint32_t)p[4 * i + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3];
    uint32_t e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t t1 = h + S1 + ch + K256[i] + w[i];
        uint32_t S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = S0 + mj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d;
    s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha_update(Sha *s, const unsigned char *p, size_t n) {
    s->len += (uint64_t)n * 8;
    while (n) {
        size_t take = 64 - s->n;
        if (take > n) take = n;
        memcpy(s->buf + s->n, p, take);
        s->n += take;
        p += take;
        n -= take;
        if (s->n == 64) {
            sha_block(s, s->buf);
            s->n = 0;
        }
    }
}

static void sha_final(Sha *s, unsigned char out[32]) {
    uint64_t bits = s->len;
    unsigned char pad = 0x80;
    sha_update(s, &pad, 1);
    unsigned char zero = 0;
    while (s->n != 56) sha_update(s, &zero, 1);
    unsigned char lenb[8];
    for (int i = 0; i < 8; i++) lenb[i] = (unsigned char)(bits >> (56 - 8 * i));
    sha_update(s, lenb, 8);
    for (int i = 0; i < 8; i++) {
        out[4 * i] = (unsigned char)(s->h[i] >> 24);
        out[4 * i + 1] = (unsigned char)(s->h[i] >> 16);
        out[4 * i + 2] = (unsigned char)(s->h[i] >> 8);
        out[4 * i + 3] = (unsigned char)s->h[i];
    }
}

static void sha_hex(const unsigned char *p, size_t n, char out[65]) {
    Sha s;
    unsigned char d[32];
    sha_init(&s);
    sha_update(&s, p, n);
    sha_final(&s, d);
    static const char *hx = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[2 * i] = hx[d[i] >> 4];
        out[2 * i + 1] = hx[d[i] & 15];
    }
    out[64] = '\0';
}

/* ------------------------------------------------------------------ JSON */

typedef enum { J_NULL, J_BOOL, J_INT, J_STR, J_ARR, J_OBJ } JKind;

typedef struct JVal JVal;
struct JVal {
    JKind kind;
    int b;
    long long i;
    char *s;      /* J_STR: decoded bytes, NUL-terminated */
    size_t slen;
    JVal **items; /* J_ARR / J_OBJ values */
    char **keys;  /* J_OBJ keys */
    size_t *klens;
    size_t n;
};

typedef struct {
    const unsigned char *p;
    const unsigned char *end;
    int depth;
} JP;

static JVal *jnew(JKind k) {
    JVal *v = (JVal *)xalloc(sizeof *v);
    v->kind = k;
    return v;
}

static void jfail(const char *why) { fail("malformed JSON: %s", why); }

static void jskip(JP *ps) {
    while (ps->p < ps->end &&
           (*ps->p == ' ' || *ps->p == '\t' || *ps->p == '\n' || *ps->p == '\r'))
        ps->p++;
}

static int hexval(int c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int jhex4(JP *ps) {
    if (ps->end - ps->p < 4) jfail("short \\u escape");
    int v = 0;
    for (int i = 0; i < 4; i++) {
        int h = hexval(ps->p[i]);
        if (h < 0) jfail("bad \\u escape");
        v = (v << 4) | h;
    }
    ps->p += 4;
    return v;
}

static void utf8_put(char *out, size_t *n, unsigned cp) {
    if (cp < 0x80) {
        out[(*n)++] = (char)cp;
    } else if (cp < 0x800) {
        out[(*n)++] = (char)(0xc0 | (cp >> 6));
        out[(*n)++] = (char)(0x80 | (cp & 0x3f));
    } else if (cp < 0x10000) {
        out[(*n)++] = (char)(0xe0 | (cp >> 12));
        out[(*n)++] = (char)(0x80 | ((cp >> 6) & 0x3f));
        out[(*n)++] = (char)(0x80 | (cp & 0x3f));
    } else {
        out[(*n)++] = (char)(0xf0 | (cp >> 18));
        out[(*n)++] = (char)(0x80 | ((cp >> 12) & 0x3f));
        out[(*n)++] = (char)(0x80 | ((cp >> 6) & 0x3f));
        out[(*n)++] = (char)(0x80 | (cp & 0x3f));
    }
}

static char *jparse_str(JP *ps, size_t *outn) {
    if (ps->p >= ps->end || *ps->p != '"') jfail("expected string");
    ps->p++;
    size_t cap = (size_t)(ps->end - ps->p) + 1;
    char *buf = (char *)xalloc(cap);
    size_t n = 0;
    while (ps->p < ps->end) {
        unsigned char c = *ps->p++;
        if (c == '"') {
            buf[n] = '\0';
            *outn = n;
            return buf;
        }
        if (c != '\\') {
            if (c < 0x20) jfail("control byte in string");
            buf[n++] = (char)c;
            continue;
        }
        if (ps->p >= ps->end) jfail("trailing backslash");
        c = *ps->p++;
        switch (c) {
        case '"': buf[n++] = '"'; break;
        case '\\': buf[n++] = '\\'; break;
        case '/': buf[n++] = '/'; break;
        case 'b': buf[n++] = '\b'; break;
        case 'f': buf[n++] = '\f'; break;
        case 'n': buf[n++] = '\n'; break;
        case 'r': buf[n++] = '\r'; break;
        case 't': buf[n++] = '\t'; break;
        case 'u': {
            unsigned cp = (unsigned)jhex4(ps);
            if (cp >= 0xd800 && cp <= 0xdbff) {
                if (ps->end - ps->p >= 6 && ps->p[0] == '\\' && ps->p[1] == 'u') {
                    ps->p += 2;
                    unsigned lo = (unsigned)jhex4(ps);
                    if (lo >= 0xdc00 && lo <= 0xdfff)
                        cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00);
                    else
                        jfail("bad surrogate pair");
                } else {
                    jfail("lone high surrogate");
                }
            }
            utf8_put(buf, &n, cp);
            break;
        }
        default: jfail("bad escape");
        }
    }
    jfail("unterminated string");
    return NULL;
}

static JVal *jparse_val(JP *ps);

static JVal *jparse_num(JP *ps) {
    const unsigned char *start = ps->p;
    if (ps->p < ps->end && *ps->p == '-') ps->p++;
    if (ps->p >= ps->end || *ps->p < '0' || *ps->p > '9') jfail("bad number");
    while (ps->p < ps->end && *ps->p >= '0' && *ps->p <= '9') ps->p++;
    if (ps->p < ps->end &&
        (*ps->p == '.' || *ps->p == 'e' || *ps->p == 'E'))
        jfail("only integer numbers are in this format");
    char tmp[64];
    size_t n = (size_t)(ps->p - start);
    if (n >= sizeof tmp) jfail("number too long");
    memcpy(tmp, start, n);
    tmp[n] = '\0';
    JVal *v = jnew(J_INT);
    v->i = strtoll(tmp, NULL, 10);
    return v;
}

static JVal *jparse_arr(JP *ps) {
    JVal *v = jnew(J_ARR);
    ps->p++; /* [ */
    size_t cap = 0;
    jskip(ps);
    if (ps->p < ps->end && *ps->p == ']') {
        ps->p++;
        return v;
    }
    for (;;) {
        if (v->n == cap) {
            cap = cap ? cap * 2 : 8;
            v->items = (JVal **)realloc(v->items, cap * sizeof *v->items);
            if (!v->items) jfail("out of memory");
        }
        v->items[v->n++] = jparse_val(ps);
        jskip(ps);
        if (ps->p < ps->end && *ps->p == ',') {
            ps->p++;
            jskip(ps);
            continue;
        }
        if (ps->p < ps->end && *ps->p == ']') {
            ps->p++;
            return v;
        }
        jfail("expected , or ]");
    }
}

static JVal *jparse_obj(JP *ps) {
    JVal *v = jnew(J_OBJ);
    ps->p++; /* { */
    size_t cap = 0;
    jskip(ps);
    if (ps->p < ps->end && *ps->p == '}') {
        ps->p++;
        return v;
    }
    for (;;) {
        jskip(ps);
        size_t klen = 0;
        char *key = jparse_str(ps, &klen);
        jskip(ps);
        if (ps->p >= ps->end || *ps->p != ':') jfail("expected :");
        ps->p++;
        jskip(ps);
        JVal *val = jparse_val(ps);
        size_t at = (size_t)-1;
        for (size_t i = 0; i < v->n; i++) {
            if (v->klens[i] == klen && memcmp(v->keys[i], key, klen) == 0) {
                at = i;
                break;
            }
        }
        if (at != (size_t)-1) {
            v->items[at] = val;
        } else {
            if (v->n == cap) {
                cap = cap ? cap * 2 : 8;
                v->items = (JVal **)realloc(v->items, cap * sizeof *v->items);
                v->keys = (char **)realloc(v->keys, cap * sizeof *v->keys);
                v->klens = (size_t *)realloc(v->klens, cap * sizeof *v->klens);
                if (!v->items || !v->keys || !v->klens) jfail("out of memory");
            }
            v->items[v->n] = val;
            v->keys[v->n] = key;
            v->klens[v->n] = klen;
            v->n++;
        }
        jskip(ps);
        if (ps->p < ps->end && *ps->p == ',') {
            ps->p++;
            continue;
        }
        if (ps->p < ps->end && *ps->p == '}') {
            ps->p++;
            return v;
        }
        jfail("expected , or }");
    }
}

static JVal *jparse_val(JP *ps) {
    if (++ps->depth > MAX_DEPTH_JSON) jfail("nesting too deep");
    jskip(ps);
    if (ps->p >= ps->end) jfail("unexpected end");
    JVal *v = NULL;
    switch (*ps->p) {
    case '{': v = jparse_obj(ps); break;
    case '[': v = jparse_arr(ps); break;
    case '"': {
        v = jnew(J_STR);
        v->s = jparse_str(ps, &v->slen);
        break;
    }
    case 't':
        if (ps->end - ps->p >= 4 && memcmp(ps->p, "true", 4) == 0) {
            ps->p += 4;
            v = jnew(J_BOOL);
            v->b = 1;
        }
        break;
    case 'f':
        if (ps->end - ps->p >= 5 && memcmp(ps->p, "false", 5) == 0) {
            ps->p += 5;
            v = jnew(J_BOOL);
            v->b = 0;
        }
        break;
    case 'n':
        if (ps->end - ps->p >= 4 && memcmp(ps->p, "null", 4) == 0) {
            ps->p += 4;
            v = jnew(J_NULL);
        }
        break;
    default:
        if (*ps->p == '-' || (*ps->p >= '0' && *ps->p <= '9')) v = jparse_num(ps);
        break;
    }
    if (!v) jfail("unexpected token");
    ps->depth--;
    return v;
}

static JVal *json_parse(const unsigned char *p, size_t n) {
    JP ps = {p, p + n, 0};
    JVal *v = jparse_val(&ps);
    jskip(&ps);
    if (ps.p != ps.end) jfail("trailing bytes");
    return v;
}

static JVal *jget(const JVal *o, const char *k) {
    if (!o || o->kind != J_OBJ) return NULL;
    size_t kl = strlen(k);
    for (size_t i = 0; i < o->n; i++)
        if (o->klens[i] == kl && memcmp(o->keys[i], k, kl) == 0) return o->items[i];
    return NULL;
}

static int jstr_is(const JVal *v, const char *s) {
    if (!v || v->kind != J_STR) return 0;
    size_t n = strlen(s);
    return v->slen == n && memcmp(v->s, s, n) == 0;
}

static int jint_is(const JVal *v, long long x) {
    return v && v->kind == J_INT && v->i == x;
}

static int jbool_is(const JVal *v, int x) {
    return v && v->kind == J_BOOL && v->b == (x ? 1 : 0);
}

static int jlen(const JVal *v) {
    if (!v || (v->kind != J_ARR && v->kind != J_OBJ)) return -1;
    return (int)v->n;
}

static JVal *jidx(const JVal *v, int i) {
    if (!v || v->kind != J_ARR || i < 0 || (size_t)i >= v->n) return NULL;
    return v->items[i];
}

/* ------------------------------------------------------------------ 规范化 JSON */

typedef struct {
    char *p;
    size_t n, cap;
} Buf;

static void bput(Buf *b, const char *s, size_t n) {
    if (b->n + n + 1 > b->cap) {
        size_t cap = b->cap ? b->cap : 256;
        while (cap < b->n + n + 1) cap *= 2;
        b->p = (char *)realloc(b->p, cap);
        if (!b->p) {
            fprintf(stderr, "ERR: out of memory\n");
            exit(2);
        }
        b->cap = cap;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = '\0';
}

static void bputs(Buf *b, const char *s) { bput(b, s, strlen(s)); }

static void bputc(Buf *b, char c) { bput(b, &c, 1); }

static void canon(JVal *v, Buf *b);

static void canon_str(const char *s, size_t n, Buf *b) {
    bputc(b, '"');
    static const char *hx = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"') {
            bputs(b, "\\\"");
        } else if (c == '\\') {
            bputs(b, "\\\\");
        } else if (c == '\b') {
            bputs(b, "\\b");
        } else if (c == '\f') {
            bputs(b, "\\f");
        } else if (c == '\n') {
            bputs(b, "\\n");
        } else if (c == '\r') {
            bputs(b, "\\r");
        } else if (c == '\t') {
            bputs(b, "\\t");
        } else if (c < 0x20) {
            char tmp[7] = {'\\', 'u', '0', '0', hx[c >> 4], hx[c & 15], '\0'};
            bputs(b, tmp);
        } else {
            bputc(b, (char)c);
        }
    }
    bputc(b, '"');
}

static int key_cmp(const JVal *v, size_t a, size_t b) {
    size_t na = v->klens[a], nb = v->klens[b];
    size_t n = na < nb ? na : nb;
    int c = memcmp(v->keys[a], v->keys[b], n);
    if (c) return c;
    return na < nb ? -1 : (na > nb ? 1 : 0);
}

static void canon_sort(JVal *v, size_t *ix) {
    for (size_t i = 1; i < v->n; i++) {
        size_t x = ix[i];
        size_t j = i;
        while (j > 0 && key_cmp(v, ix[j - 1], x) > 0) {
            ix[j] = ix[j - 1];
            j--;
        }
        ix[j] = x;
    }
}

static void canon(JVal *v, Buf *b) {
    if (!v) {
        bputs(b, "null");
        return;
    }
    switch (v->kind) {
    case J_NULL: bputs(b, "null"); break;
    case J_BOOL: bputs(b, v->b ? "true" : "false"); break;
    case J_INT: {
        char tmp[32];
        snprintf(tmp, sizeof tmp, "%lld", v->i);
        bputs(b, tmp);
        break;
    }
    case J_STR: canon_str(v->s, v->slen, b); break;
    case J_ARR:
        bputc(b, '[');
        for (size_t i = 0; i < v->n; i++) {
            if (i) bputc(b, ',');
            canon(v->items[i], b);
        }
        bputc(b, ']');
        break;
    case J_OBJ: {
        bputc(b, '{');
        size_t *ix = (size_t *)xalloc((v->n ? v->n : 1) * sizeof *ix);
        for (size_t i = 0; i < v->n; i++) ix[i] = i;
        canon_sort(v, ix);
        for (size_t i = 0; i < v->n; i++) {
            if (i) bputc(b, ',');
            canon_str(v->keys[ix[i]], v->klens[ix[i]], b);
            bputc(b, ':');
            canon(v->items[ix[i]], b);
        }
        free(ix);
        bputc(b, '}');
        break;
    }
    }
}

static char *canon_dup(JVal *v) {
    Buf b = {0, 0, 0};
    canon(v, &b);
    if (!b.p) b.p = xstrdup("");
    return b.p;
}

static int canon_eq(JVal *a, JVal *b) {
    char *x = canon_dup(a), *y = canon_dup(b);
    int ok = strcmp(x, y) == 0;
    free(x);
    free(y);
    return ok;
}

static int sha_canon(JVal *v, char out[65]) {
    char *c = canon_dup(v);
    sha_hex((const unsigned char *)c, strlen(c), out);
    free(c);
    return 0;
}

/* ------------------------------------------------------------------ 十六进制 */

static unsigned char *hex_decode(const JVal *v, size_t *outn) {
    if (!v || v->kind != J_STR) fail("hex field is not a string%s", "");
    size_t cap = v->slen / 2 + 1;
    unsigned char *out = (unsigned char *)xalloc(cap);
    size_t n = 0;
    int hi = -1;
    for (size_t i = 0; i < v->slen; i++) {
        unsigned char c = (unsigned char)v->s[i];
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') continue;
        int h = hexval(c);
        if (h < 0) fail("hex field has a non-hex byte%s", "");
        if (hi < 0) {
            hi = h;
        } else {
            out[n++] = (unsigned char)((hi << 4) | h);
            hi = -1;
        }
    }
    if (hi >= 0) fail("hex field has an odd number of digits%s", "");
    *outn = n;
    return out;
}

static int is_hex64(const char *s) {
    if (!s || strlen(s) != 64) return 0;
    for (int i = 0; i < 64; i++)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return 0;
    return 1;
}

/* ------------------------------------------------------------------ 玩具指令 */

typedef enum { OP_PUSH = 1, OP_ADD = 2, OP_MUL = 3, OP_PRINT = 4, OP_HALT = 5 } Op;

static const char *OPNAME[6] = {NULL, "PUSH", "ADD", "MUL", "PRINT", "HALT"};

static int op_by_name(const char *s, size_t n) {
    for (int i = 1; i <= 5; i++)
        if (strlen(OPNAME[i]) == n && memcmp(OPNAME[i], s, n) == 0) return i;
    return 0;
}

static int op_arity(int op) { return op == OP_PUSH ? 1 : 0; }

typedef struct {
    int op;
    int arg;
} Ins;

typedef struct {
    Ins *v;
    int n;
    int cap;
} Unit;

static void unit_push(Unit *u, int op, int arg) {
    if (u->n == u->cap) {
        u->cap = u->cap ? u->cap * 2 : 16;
        u->v = (Ins *)realloc(u->v, (size_t)u->cap * sizeof *u->v);
        if (!u->v) {
            fprintf(stderr, "ERR: out of memory\n");
            exit(2);
        }
    }
    u->v[u->n].op = op;
    u->v[u->n].arg = arg;
    u->n++;
}

static int is_space(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

static int is_digit(unsigned char c) { return c >= '0' && c <= '9'; }

static int utf8_valid(const unsigned char *p, size_t n) {
    size_t i = 0;
    while (i < n) {
        unsigned char c = p[i++];
        if (c < 0x80) continue;
        int need;
        unsigned cp;
        if (c >= 0xc2 && c <= 0xdf) {
            need = 1;
            cp = c & 0x1f;
        } else if (c >= 0xe0 && c <= 0xef) {
            need = 2;
            cp = c & 0x0f;
        } else if (c >= 0xf0 && c <= 0xf4) {
            need = 3;
            cp = c & 0x07;
        } else {
            return 0;
        }
        if (i + (size_t)need > n) return 0;
        for (int k = 0; k < need; k++) {
            unsigned char d = p[i++];
            if ((d & 0xc0) != 0x80) return 0;
            cp = (cp << 6) | (d & 0x3f);
        }
        if (need == 2 && (cp < 0x800 || (cp >= 0xd800 && cp <= 0xdfff))) return 0;
        if (need == 3 && (cp < 0x10000 || cp > 0x10ffff)) return 0;
    }
    return 1;
}

static void parse_toy(const unsigned char *src, size_t n, Unit *out) {
    if (!utf8_valid(src, n)) fail("PARSE: source is not UTF-8%s", "");
    size_t i = 0;
    long long line = 0;
    while (i < n) {
        line++;
        size_t start = i;
        while (i < n && src[i] != '\n' && src[i] != '\r') i++;
        size_t stop = i;
        if (i < n) {
            if (src[i] == '\r' && i + 1 < n && src[i + 1] == '\n') i += 2;
            else i++;
        }
        /* 注释从第一个 '#' 起 */
        for (size_t k = start; k < stop; k++)
            if (src[k] == '#') {
                stop = k;
                break;
            }
        size_t a = start, b = stop;
        while (a < b && is_space(src[a])) a++;
        while (b > a && is_space(src[b - 1])) b--;
        if (a == b) continue;
        size_t f0 = a;
        while (a < b && !is_space(src[a])) a++;
        size_t f0n = a - f0;
        if (f0n == 0) continue;
        char *word = xstrndup((const char *)src + f0, f0n);
        for (size_t k = 0; k < f0n; k++)
            if (word[k] >= 'a' && word[k] <= 'z') word[k] = (char)(word[k] - 'a' + 'A');
        int op = op_by_name(word, f0n);
        free(word);
        if (!op) {
            fprintf(stdout, "REJECT: line %lld: unknown op\n", line);
            exit(1);
        }
        while (a < b && is_space(src[a])) a++;
        if (op_arity(op) == 0) {
            if (a != b) {
                fprintf(stdout, "REJECT: line %lld: %s takes no argument\n", line,
                        OPNAME[op]);
                exit(1);
            }
            unit_push(out, op, 0);
        } else {
            if (a == b) {
                fprintf(stdout, "REJECT: line %lld: %s needs one argument\n", line,
                        OPNAME[op]);
                exit(1);
            }
            size_t g0 = a;
            while (a < b && !is_space(src[a])) a++;
            size_t g0n = a - g0;
            for (size_t k = 0; k < g0n; k++)
                if (!is_digit(src[g0 + k])) {
                    fprintf(stdout, "REJECT: line %lld: argument is not an integer\n",
                            line);
                    exit(1);
                }
            long long arg = 0;
            for (size_t k = 0; k < g0n; k++) arg = arg * 10 + (src[g0 + k] - '0');
            if (arg < 0 || arg > 255) {
                fprintf(stdout, "REJECT: line %lld: argument out of byte range\n", line);
                exit(1);
            }
            while (a < b && is_space(src[a])) a++;
            if (a != b) {
                fprintf(stdout, "REJECT: line %lld: too many fields\n", line);
                exit(1);
            }
            unit_push(out, op, (int)arg);
        }
    }
    if (out->n == 0) fail("PARSE: empty source%s", "");
    if (out->v[out->n - 1].op != OP_HALT) fail("PARSE: last instruction must be HALT%s", "");
}

static void decode_unit(const unsigned char *b, size_t n, Unit *out) {
    if (n % 2) fail("TV-L2: unit byte length is not a multiple of 2%s", "");
    for (size_t i = 0; i < n; i += 2) {
        int op = b[i], arg = b[i + 1];
        if (op < 1 || op > 5) fail("TV-L2: unknown opcode%s", "");
        if (op_arity(op) == 0 && arg != 0)
            fail("TV-L2: a zero-arity op carries a non-zero operand%s", "");
        unit_push(out, op, arg);
    }
    if (out->n == 0 || out->v[out->n - 1].op != OP_HALT)
        fail("TV-L2: unit must end with HALT%s", "");
}

static JVal *unit_json(const Unit *u) {
    JVal *a = jnew(J_ARR);
    a->items = (JVal **)xalloc((size_t)(u->n ? u->n : 1) * sizeof *a->items);
    for (int i = 0; i < u->n; i++) {
        JVal *o = jnew(J_OBJ);
        o->keys = (char **)xalloc(2 * sizeof *o->keys);
        o->klens = (size_t *)xalloc(2 * sizeof *o->klens);
        o->items = (JVal **)xalloc(2 * sizeof *o->items);
        o->keys[0] = xstrdup("arg");
        o->klens[0] = 3;
        o->items[0] = jnew(J_INT);
        o->items[0]->i = u->v[i].arg;
        o->keys[1] = xstrdup("op");
        o->klens[1] = 2;
        o->items[1] = jnew(J_STR);
        o->items[1]->s = xstrdup(OPNAME[u->v[i].op]);
        o->items[1]->slen = strlen(OPNAME[u->v[i].op]);
        o->n = 2;
        a->items[i] = o;
    }
    a->n = (size_t)u->n;
    return a;
}

static int static_check(const Unit *u) {
    int depth = 0, peak = 0;
    for (int i = 0; i < u->n; i++) {
        int op = u->v[i].op;
        if (op == OP_PUSH) {
            depth++;
        } else if (op == OP_ADD || op == OP_MUL) {
            if (depth < 2) fail("SAFE: an arithmetic op underflows the stack%s", "");
            depth--;
        } else if (op == OP_PRINT) {
            if (depth < 1) fail("SAFE: PRINT underflows the stack%s", "");
            depth--;
        } else if (op == OP_HALT) {
            if (depth != 0) fail("SAFE: HALT with a non-empty stack%s", "");
        }
        if (depth > peak) peak = depth;
        if (peak > STACK_MAX) fail("SAFE: stack depth exceeds the limit%s", "");
    }
    return peak;
}

static int run_unit(const Unit *u, long long *out, int cap, int *outn) {
    int n = 0;
    long long st[STACK_MAX];
    int sp = 0;
    for (int i = 0; i < u->n; i++) {
        int op = u->v[i].op;
        if (op == OP_PUSH) {
            if (sp == STACK_MAX) fail("SEM: stack overflow%s", "");
            st[sp++] = u->v[i].arg;
        } else if (op == OP_ADD || op == OP_MUL) {
            if (sp < 2) fail("SEM: an arithmetic op underflows at run time%s", "");
            long long b = st[--sp], a = st[--sp];
            __int128 r = (op == OP_ADD) ? (__int128)a + b : (__int128)a * b;
            if (r < -9223372036854775807LL - 1 || r > 9223372036854775807LL)
                fail("SEM: integer overflow (the Python kernel is unbounded)%s", "");
            st[sp++] = (long long)r;
        } else if (op == OP_PRINT) {
            if (sp < 1) fail("SEM: PRINT underflows at run time%s", "");
            if (n == cap) fail("SEM: too many outputs%s", "");
            out[n++] = st[--sp];
        } else if (op == OP_HALT) {
            break;
        }
    }
    *outn = n;
    return 0;
}

/* ------------------------------------------------------------------ 产物 */

typedef struct {
    Unit units[MAX_UNITS];
    int n;
} Program;

static void program_from_container_bytes(const unsigned char *raw, size_t n, Program *p) {
    memset(p, 0, sizeof *p);
    JVal *doc = json_parse(raw, n);
    if (!jstr_is(jget(doc, "format"), "toy/1"))
        fail("artifact format is not toy/1%s", "");
    JVal *units = jget(doc, "units");
    int un = jlen(units);
    if (un <= 0) fail("artifact has no units%s", "");
    if (un > MAX_UNITS) fail("artifact has too many units%s", "");
    for (int i = 0; i < un; i++) {
        JVal *u = jidx(units, i);
        if (!u || u->kind != J_OBJ) fail("artifact unit is malformed%s", "");
        size_t bn = 0;
        unsigned char *b = hex_decode(jget(u, "hex"), &bn);
        char h[65];
        sha_hex(b, bn, h);
        JVal *want = jget(u, "sha256");
        if (!jstr_is(want, h)) fail("artifact unit sha256 mismatch%s", "");
        decode_unit(b, bn, &p->units[i]);
        free(b);
    }
    p->n = un;
}

/* ------------------------------------------------------------------ 节点图 */

typedef struct {
    char *id;
    JVal *judgment;
    int prem[MAX_PREM];
    int nprem;
} Node;

typedef struct {
    Node *nodes;
    int n;
    Program prog;
    char *artifact_sha;
} Ctx;

static int find_node(Ctx *c, const char *id) {
    for (int i = 0; i < c->n; i++)
        if (strcmp(c->nodes[i].id, id) == 0) return i;
    return -1;
}

static int kind_is(JVal *j, const char *k) { return jstr_is(jget(j, "kind"), k); }

static int find_linked(Ctx *c, int idx, int *seen, int depth) {
    if (depth > 256) fail("CAP: premise chain is too deep%s", "");
    if (idx < 0 || idx >= c->n || seen[idx]) return -1;
    seen[idx] = 1;
    if (kind_is(c->nodes[idx].judgment, "Linked")) return idx;
    for (int i = 0; i < c->nodes[idx].nprem; i++) {
        int got = find_linked(c, c->nodes[idx].prem[i], seen, depth + 1);
        if (got >= 0) return got;
    }
    return -1;
}

static int linked_of(Ctx *c, int idx) {
    int *seen = (int *)xalloc((size_t)c->n * sizeof *seen);
    for (int i = 0; i < c->nodes[idx].nprem; i++) {
        int got = find_linked(c, c->nodes[idx].prem[i], seen, 0);
        if (got >= 0) {
            free(seen);
            return got;
        }
    }
    free(seen);
    fail("CAP: no Linked judgment in the premise chain%s", "");
    return -1;
}

static void collect(Ctx *c, int idx, const char *kind, int *seen, JVal **out, int *n,
                    int cap) {
    if (idx < 0 || idx >= c->n || seen[idx]) return;
    seen[idx] = 1;
    if (kind_is(c->nodes[idx].judgment, kind)) {
        if (*n == cap) fail("derivation has too many judgments%s", "");
        out[(*n)++] = c->nodes[idx].judgment;
    }
    for (int i = 0; i < c->nodes[idx].nprem; i++)
        collect(c, c->nodes[idx].prem[i], kind, seen, out, n, cap);
}

/* ------------------------------------------------------------------ 规则 */

/* ------------------------------------------------------------------ 偏差站点 (R5) */

static JVal *dev_obj(int unit, int index) {
    JVal *o = jnew(J_OBJ);
    o->keys = (char **)xalloc(3 * sizeof *o->keys);
    o->klens = (size_t *)xalloc(3 * sizeof *o->klens);
    o->items = (JVal **)xalloc(3 * sizeof *o->items);
    o->keys[0] = xstrdup("unit");
    o->klens[0] = 4;
    o->items[0] = jnew(J_INT);
    o->items[0]->i = unit;
    o->keys[1] = xstrdup("index");
    o->klens[1] = 5;
    o->items[1] = jnew(J_INT);
    o->items[1]->i = index;
    o->keys[2] = xstrdup("op");
    o->klens[2] = 2;
    o->items[2] = jnew(J_STR);
    o->items[2]->s = xstrdup("PRINT");
    o->items[2]->slen = 5;
    o->n = 3;
    return o;
}

static JVal *deviations_json(const Program *p) {
    int count = 0;
    for (int u = 0; u < p->n; u++)
        for (int i = 0; i < p->units[u].n; i++)
            if (p->units[u].v[i].op == OP_PRINT) count++;
    JVal *a = jnew(J_ARR);
    a->items = (JVal **)xalloc((size_t)(count ? count : 1) * sizeof *a->items);
    int k = 0;
    for (int u = 0; u < p->n; u++)
        for (int i = 0; i < p->units[u].n; i++)
            if (p->units[u].v[i].op == OP_PRINT) a->items[k++] = dev_obj(u, i);
    a->n = (size_t)k;
    return a;
}

static int dev_key(const JVal *d, int *u, int *i) {
    JVal *ju = jget(d, "unit"), *ji = jget(d, "index");
    if (!ju || ju->kind != J_INT || !ji || ji->kind != J_INT) return 0;
    *u = (int)ju->i;
    *i = (int)ji->i;
    return 1;
}

static int dev_has(const JVal *devs, int u, int i) {
    for (size_t k = 0; k < devs->n; k++) {
        int du, di;
        if (dev_key(devs->items[k], &du, &di) && du == u && di == i) return 1;
    }
    return 0;
}

/* 返回 devs 里 key 不在 other 中的项（保持 devs 的顺序）。 */
static JVal *dev_missing(JVal *devs, JVal *other) {
    JVal *a = jnew(J_ARR);
    a->items = (JVal **)xalloc((devs->n ? devs->n : 1) * sizeof *a->items);
    size_t k = 0;
    for (size_t j = 0; j < devs->n; j++) {
        int u, i;
        if (dev_key(devs->items[j], &u, &i) && !dev_has(other, u, i))
            a->items[k++] = devs->items[j];
    }
    a->n = k;
    return a;
}

static JVal *r_refine(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    if (np != 1 || !kind_is(pre[0], "Repro"))
        fail("REFINE: needs one Repro premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("REFINE: judgment is not an object%s", "");
    if (!kind_is(j, "Refines")) fail("REFINE: judgment is not Refines%s", "");
    if (!jstr_is(jget(j, "artifact_sha256"), c->artifact_sha))
        fail("REFINE: the new artifact hash does not match%s", "");
    JVal *fh = jget(j, "from_artifact_sha256");
    if (!fh || fh->kind != J_STR || !is_hex64(fh->s))
        fail("REFINE: from_artifact_sha256 is not a sha256%s", "");
    size_t on = 0;
    unsigned char *ob = hex_decode(jget(j, "from_container_hex"), &on);
    char oh[65];
    sha_hex(ob, on, oh);
    if (!jstr_is(fh, oh))
        fail("REFINE: the predecessor container hash does not match%s", "");
    int li = linked_of(c, (int)(self - c->nodes));
    size_t nn = 0;
    unsigned char *nb = hex_decode(jget(c->nodes[li].judgment, "container_hex"), &nn);
    Program newp, oldp;
    program_from_container_bytes(nb, nn, &newp);
    program_from_container_bytes(ob, on, &oldp);
    JVal *dn = deviations_json(&newp), *df = deviations_json(&oldp);
    if (!canon_eq(jget(j, "to_deviations"), dn))
        fail("REFINE: to_deviations do not match the new artifact%s", "");
    if (!canon_eq(jget(j, "from_deviations"), df))
        fail("REFINE: from_deviations do not match the predecessor%s", "");
    JVal *added = dev_missing(dn, df);
    JVal *removed = dev_missing(df, dn);
    JVal *delta = jget(j, "delta");
    if (!delta || delta->kind != J_OBJ) fail("REFINE: delta is not an object%s", "");
    if (!canon_eq(jget(delta, "added"), added) || !canon_eq(jget(delta, "removed"), removed))
        fail("REFINE: delta does not match the two deviation sets%s", "");
    if (added->n > 0) fail("REFINE: the update adds deviations%s", "");
    free(ob);
    free(nb);
    return j;
}

static char *RULES_SHA = NULL;

static void init_rules_sha(void) {
    static const char *lit =
        "{\"format\":1,\"rules\":[\"AX-K\",\"AX-S\",\"PARSE\",\"TV-L1\",\"TV-L2\","
        "\"LINK\",\"SAFE\",\"CAP\",\"SEM\",\"COMPOSE\",\"PROV\",\"REPRO\",\"REFINE\"]}";
    RULES_SHA = (char *)xalloc(65);
    sha_hex((const unsigned char *)lit, strlen(lit), RULES_SHA);
}

static JVal *r_ax_k(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)pre; (void)np; (void)c; (void)self;
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("AX-K: judgment is not an object%s", "");
    if (!kind_is(j, "Kernel")) fail("AX-K: judgment is not Kernel%s", "");
    JVal *h = jget(j, "rules_sha256");
    if (!jstr_is(h, RULES_SHA)) fail("AX-K: rule-table hash mismatch%s", "");
    return j;
}

static JVal *r_ax_s(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Kernel"))
        fail("AX-S: needs one Kernel premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("AX-S: judgment is not an object%s", "");
    if (!kind_is(j, "Compiler")) fail("AX-S: judgment is not Compiler%s", "");
    JVal *s = jget(j, "seed_sha256");
    if (!s || s->kind != J_STR || !is_hex64(s->s))
        fail("AX-S: seed_sha256 is not a sha256%s", "");
    return j;
}

static JVal *r_parse(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Compiler"))
        fail("PARSE: needs one Compiler premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("PARSE: judgment is not an object%s", "");
    if (!kind_is(j, "Source")) fail("PARSE: judgment is not Source%s", "");
    size_t sn = 0;
    unsigned char *src = hex_decode(jget(j, "source_hex"), &sn);
    char h[65];
    sha_hex(src, sn, h);
    if (!jstr_is(jget(j, "source_sha256"), h))
        fail("PARSE: source hash mismatch%s", "");
    Unit u = {0};
    parse_toy(src, sn, &u);
    JVal *ast = jget(j, "ast");
    if (!ast || ast->kind != J_ARR) fail("PARSE: ast is not an array%s", "");
    JVal *want = unit_json(&u);
    if (!canon_eq(ast, want)) fail("PARSE: AST does not match the source%s", "");
    free(src);
    return j;
}

static JVal *r_tv_l1(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Source"))
        fail("TV-L1: needs one Source premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("TV-L1: judgment is not an object%s", "");
    if (!kind_is(j, "IR")) fail("TV-L1: judgment is not IR%s", "");
    JVal *ast = jget(pre[0], "ast");
    char h[65];
    sha_canon(ast, h);
    if (!jstr_is(jget(j, "ast_sha256"), h)) fail("TV-L1: AST hash mismatch%s", "");
    JVal *ir = jget(j, "ir");
    if (!canon_eq(ir, ast)) fail("TV-L1: IR is not the declared translation of the AST%s", "");
    JVal *map = jget(j, "map");
    int mn = jlen(map), an = jlen(ast);
    if (mn != an) fail("TV-L1: map length mismatch%s", "");
    for (int i = 0; i < mn; i++) {
        JVal *e = jidx(map, i), *x = jidx(ast, i);
        if (!e || e->kind != J_OBJ) fail("TV-L1: map entry is not an object%s", "");
        JVal *xop = x ? jget(x, "op") : NULL;
        JVal *xarg = x ? jget(x, "arg") : NULL;
        if (!xop || xop->kind != J_STR || !xarg || xarg->kind != J_INT)
            fail("TV-L1: the AST entry is malformed%s", "");
        if (!jint_is(jget(e, "index"), i) ||
            !jstr_is(jget(e, "op"), xop->s) ||
            !jint_is(jget(e, "arg"), xarg->i) ||
            !jint_is(jget(e, "offset"), 2LL * i))
            fail("TV-L1: a map entry does not match the AST%s", "");
    }
    return j;
}

static JVal *r_tv_l2(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "IR"))
        fail("TV-L2: needs one IR premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("TV-L2: judgment is not an object%s", "");
    if (!kind_is(j, "Unit")) fail("TV-L2: judgment is not Unit%s", "");
    JVal *ir = jget(pre[0], "ir");
    char h[65];
    sha_canon(ir, h);
    if (!jstr_is(jget(j, "ir_sha256"), h)) fail("TV-L2: IR hash mismatch%s", "");
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(j, "bytes_hex"), &bn);
    Unit u = {0};
    decode_unit(b, bn, &u);
    JVal *want = unit_json(&u);
    if (!canon_eq(want, ir)) fail("TV-L2: bytes do not encode the IR%s", "");
    sha_hex(b, bn, h);
    if (!jstr_is(jget(j, "unit_sha256"), h)) fail("TV-L2: unit hash mismatch%s", "");
    free(b);
    return j;
}

static JVal *r_link(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np < 1) fail("LINK: needs Unit premises%s", "");
    for (int i = 0; i < np; i++)
        if (!kind_is(pre[i], "Unit")) fail("LINK: needs Unit premises%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("LINK: judgment is not an object%s", "");
    if (!kind_is(j, "Linked")) fail("LINK: judgment is not Linked%s", "");
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(j, "container_hex"), &bn);
    char h[65];
    sha_hex(b, bn, h);
    if (!jstr_is(jget(j, "container_sha256"), h))
        fail("LINK: container hash mismatch%s", "");
    Program p;
    program_from_container_bytes(b, bn, &p);
    JVal *units = jget(j, "units");
    if (jlen(units) != np) fail("LINK: unit count mismatch%s", "");
    for (int i = 0; i < np; i++) {
        JVal *u = jidx(units, i);
        JVal *want = jget(pre[i], "unit_sha256");
        if (!u || u->kind != J_STR || !want || want->kind != J_STR ||
            !jstr_is(u, want->s))
            fail("LINK: unit list does not match the premises%s", "");
    }
    free(b);
    return j;
}

static JVal *r_safe(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Linked"))
        fail("SAFE: needs one Linked premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("SAFE: judgment is not an object%s", "");
    if (!kind_is(j, "Safe") || !jbool_is(jget(j, "ok"), 1))
        fail("SAFE: judgment is not Safe/ok%s", "");
    JVal *l0 = jget(j, "l0_safe");
    if (l0 && l0->kind != J_NULL)
        unsupported("the C kernel does not cover the L0-safe extension");
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(pre[0], "container_hex"), &bn);
    Program p;
    program_from_container_bytes(b, bn, &p);
    JVal *md = jget(j, "max_depth");
    if (jlen(md) != p.n) fail("SAFE: max_depth length mismatch%s", "");
    for (int i = 0; i < p.n; i++)
        if (!jint_is(jidx(md, i), static_check(&p.units[i])))
            fail("SAFE: max_depth does not match the static check%s", "");
    free(b);
    return j;
}

static int caps_of(Program *p, char **out, int cap) {
    int n = 0;
    for (int i = 0; i < p->n; i++)
        for (int k = 0; k < p->units[i].n; k++)
            if (p->units[i].v[k].op == OP_PRINT) {
                int have = 0;
                for (int x = 0; x < n; x++)
                    if (strcmp(out[x], "print") == 0) have = 1;
                if (!have) {
                    if (n == cap) fail("CAP: too many capabilities%s", "");
                    out[n++] = xstrdup("print");
                }
            }
    return n;
}

/* CAP 的 judgment 形状在这里查，Linked 的核对在 cap_check()（需要节点索引）。 */
static JVal *r_cap(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Safe")) fail("CAP: needs one Safe premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("CAP: judgment is not an object%s", "");
    if (!kind_is(j, "Caps")) fail("CAP: judgment is not Caps%s", "");
    return j;
}

static JVal *r_sem(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    if (np != 1 || !kind_is(pre[0], "Caps")) fail("SEM: needs one Caps premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("SEM: judgment is not an object%s", "");
    if (!kind_is(j, "Sem")) fail("SEM: judgment is not Sem%s", "");
    int li = linked_of(c, (int)(self - c->nodes));
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(c->nodes[li].judgment, "container_hex"), &bn);
    Program p;
    program_from_container_bytes(b, bn, &p);
    long long out[MAX_INS];
    int nout = 0;
    for (int i = 0; i < p.n; i++) {
        long long uo[MAX_INS];
        int un = 0;
        run_unit(&p.units[i], uo, MAX_INS, &un);
        for (int k = 0; k < un; k++) {
            if (nout == MAX_INS) fail("SEM: too many outputs%s", "");
            out[nout++] = uo[k];
        }
    }
    JVal *want = jget(j, "output");
    if (jlen(want) != nout) fail("SEM: output length mismatch%s", "");
    for (int i = 0; i < nout; i++)
        if (!jint_is(jidx(want, i), out[i]))
            fail("SEM: output does not match the interpreter%s", "");
    if (!jint_is(jget(j, "exit"), 0)) fail("SEM: exit is not 0%s", "");
    free(b);
    return j;
}

static JVal *r_compose(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    if (np != 1 || !kind_is(pre[0], "Sem")) fail("COMPOSE: needs one Sem premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("COMPOSE: judgment is not an object%s", "");
    if (!kind_is(j, "Program")) fail("COMPOSE: judgment is not Program%s", "");
    if (!canon_eq(jget(j, "output"), jget(pre[0], "output")))
        fail("COMPOSE: output does not match Sem%s", "");
    int li = linked_of(c, (int)(self - c->nodes));
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(c->nodes[li].judgment, "container_hex"), &bn);
    Program p;
    program_from_container_bytes(b, bn, &p);
    char *caps[16];
    int cn = caps_of(&p, caps, 16);
    JVal *jc = jget(j, "caps");
    if (jlen(jc) != cn) fail("COMPOSE: caps do not match the artifact%s", "");
    for (int i = 0; i < cn; i++)
        if (!jstr_is(jidx(jc, i), caps[i]))
            fail("COMPOSE: caps do not match the artifact%s", "");
    if (!jint_is(jget(j, "units"), p.n)) fail("COMPOSE: unit count mismatch%s", "");
    free(b);
    return j;
}

static JVal *r_prov(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c;
    if (np != 1 || !kind_is(pre[0], "Program"))
        fail("PROV: needs one Program premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("PROV: judgment is not an object%s", "");
    if (!kind_is(j, "Prov")) fail("PROV: judgment is not Prov%s", "");
    JVal *l0 = jget(j, "l0");
    if (l0 && l0->kind != J_NULL)
        unsupported("the C kernel does not cover the L0 provenance extension");
    int *seen = (int *)xalloc((size_t)c->n * sizeof *seen);
    JVal *src[256], *comp[256];
    int ns = 0, nc = 0;
    for (int i = 0; i < self->nprem; i++)
        collect(c, self->prem[i], "Source", seen, src, &ns, 256);
    free(seen);
    seen = (int *)xalloc((size_t)c->n * sizeof *seen);
    for (int i = 0; i < self->nprem; i++)
        collect(c, self->prem[i], "Compiler", seen, comp, &nc, 256);
    free(seen);
    if (nc != 1) fail("PROV: need exactly one Compiler judgment%s", "");
    char *hashes[256];
    for (int i = 0; i < ns; i++) {
        JVal *h = jget(src[i], "source_sha256");
        if (!h || h->kind != J_STR) fail("PROV: Source has no hash%s", "");
        hashes[i] = h->s;
    }
    for (int i = 1; i < ns; i++) {
        char *x = hashes[i];
        int k = i;
        while (k > 0 && strcmp(hashes[k - 1], x) > 0) {
            hashes[k] = hashes[k - 1];
            k--;
        }
        hashes[k] = x;
    }
    JVal *want = jget(j, "source_sha256");
    if (jlen(want) != ns) fail("PROV: source hash list length mismatch%s", "");
    for (int i = 0; i < ns; i++)
        if (!jstr_is(jidx(want, i), hashes[i]))
            fail("PROV: source hash list mismatch%s", "");
    JVal *seed = jget(comp[0], "seed_sha256");
    if (!seed || seed->kind != J_STR || !jstr_is(jget(j, "seed_sha256"), seed->s))
        fail("PROV: seed hash mismatch%s", "");
    JVal *r = jget(j, "recipe_sha256");
    if (!r || r->kind != J_STR || !is_hex64(r->s))
        fail("PROV: recipe_sha256 is not a sha256%s", "");
    if (!jstr_is(jget(j, "artifact_sha256"), c->artifact_sha))
        fail("PROV: artifact hash mismatch%s", "");
    return j;
}

static JVal *r_repro(JVal *n, JVal **pre, int np, Ctx *c, Node *self) {
    (void)c; (void)self;
    if (np != 1 || !kind_is(pre[0], "Prov")) fail("REPRO: needs one Prov premise%s", "");
    JVal *j = jget(n, "judgment");
    if (!j || j->kind != J_OBJ) fail("REPRO: judgment is not an object%s", "");
    if (!kind_is(j, "Repro")) fail("REPRO: judgment is not Repro%s", "");
    if (!jstr_is(jget(j, "artifact_sha256"), c->artifact_sha))
        fail("REPRO: artifact hash mismatch%s", "");
    JVal *tw = jget(j, "twice");
    if (!tw || !jstr_is(tw, c->artifact_sha))
        fail("REPRO: the two builds differ%s", "");
    return j;
}

/* CAP 与 COMPOSE/SEM 共用 Linked；CAP 的具体检查需要节点索引。 */

static void cap_check(JVal *n, Ctx *c, Node *self) {
    JVal *j = jget(n, "judgment");
    int li = linked_of(c, (int)(self - c->nodes));
    size_t bn = 0;
    unsigned char *b = hex_decode(jget(c->nodes[li].judgment, "container_hex"), &bn);
    Program p;
    program_from_container_bytes(b, bn, &p);
    char *caps[16];
    int cn = caps_of(&p, caps, 16);
    JVal *req = jget(j, "required");
    if (jlen(req) != cn) fail("CAP: required set does not match the artifact%s", "");
    for (int i = 0; i < cn; i++)
        if (!jstr_is(jidx(req, i), caps[i]))
            fail("CAP: required set does not match the artifact%s", "");
    JVal *allow = jget(j, "allowed");
    int an = jlen(allow);
    if (an < 0) fail("CAP: allowed is not a string list%s", "");
    for (int i = 0; i < cn; i++) {
        int found = 0;
        for (int k = 0; k < an; k++) {
            JVal *a = jidx(allow, k);
            if (!a || a->kind != J_STR) fail("CAP: allowed is not a string list%s", "");
            if (jstr_is(a, caps[i])) found = 1;
        }
        if (!found) fail("CAP: required caps are not a subset of allowed%s", "");
    }
    free(b);
}

/* ------------------------------------------------------------------ 驱动 */

static const char *rule_of(JVal *n) {
    JVal *r = jget(n, "rule");
    return (r && r->kind == J_STR) ? r->s : NULL;
}

static unsigned char *read_file(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) ioerr(path);
    if (fseek(f, 0, SEEK_END) != 0) ioerr(path);
    long sz = ftell(f);
    if (sz < 0) ioerr(path);
    rewind(f);
    unsigned char *b = (unsigned char *)xalloc((size_t)sz + 1);
    if (sz > 0 && fread(b, 1, (size_t)sz, f) != (size_t)sz) ioerr(path);
    fclose(f);
    *n = (size_t)sz;
    return b;
}

static int do_verify(const char *art_path, const char *der_path, const char *conc_path) {
    size_t an = 0, dn = 0;
    unsigned char *art = read_file(art_path, &an);
    unsigned char *der = read_file(der_path, &dn);
    Ctx c;
    memset(&c, 0, sizeof c);
    char ah[65];
    sha_hex(art, an, ah);
    c.artifact_sha = xstrdup(ah);
    program_from_container_bytes(art, an, &c.prog);

    JVal *d = json_parse(der, dn);
    if (!jint_is(jget(d, "format"), 1)) fail("derivation format mismatch%s", "");
    JVal *kern = jget(d, "kernel");
    if (!jstr_is(jget(kern, "rules_sha256"), RULES_SHA))
        fail("derivation was checked against another rule table%s", "");
    JVal *nodes = jget(d, "nodes");
    int nn = jlen(nodes);
    if (nn <= 0) fail("derivation has no nodes%s", "");
    if (nn > MAX_NODES) fail("derivation has too many nodes%s", "");
    c.nodes = (Node *)xalloc((size_t)nn * sizeof *c.nodes);
    c.n = 0;
    for (int i = 0; i < nn; i++) {
        JVal *n = jidx(nodes, i);
        if (!n || n->kind != J_OBJ) fail("malformed node%s", "");
        JVal *idv = jget(n, "id");
        if (!idv || idv->kind != J_STR) fail("malformed node id%s", "");
        if (find_node(&c, idv->s) >= 0) fail("duplicate node id%s", "");
        const char *rule = rule_of(n);
        if (!rule) fail("node has no rule%s", "");
        JVal *j = jget(n, "judgment");
        if (!j || j->kind != J_OBJ) fail("node has no judgment object%s", "");
        Node *node = &c.nodes[c.n];
        node->id = idv->s;
        node->judgment = j;
        node->nprem = 0;
        JVal *pre = jget(n, "premises");
        int np = pre ? jlen(pre) : 0;
        if (np < 0 || np > MAX_PREM) fail("malformed premises%s", "");
        JVal *pj[MAX_PREM];
        for (int k = 0; k < np; k++) {
            JVal *p = jidx(pre, k);
            if (!p || p->kind != J_STR) fail("premise is not a node id%s", "");
            int at = find_node(&c, p->s);
            if (at < 0) fail("premise is missing or comes later%s", "");
            node->prem[k] = at;
            pj[k] = c.nodes[at].judgment;
        }
        node->nprem = np;
        if (strcmp(rule, "AX-K") == 0) r_ax_k(n, pj, np, &c, node);
        else if (strcmp(rule, "AX-S") == 0) r_ax_s(n, pj, np, &c, node);
        else if (strcmp(rule, "PARSE") == 0) r_parse(n, pj, np, &c, node);
        else if (strcmp(rule, "TV-L1") == 0) r_tv_l1(n, pj, np, &c, node);
        else if (strcmp(rule, "TV-L2") == 0) r_tv_l2(n, pj, np, &c, node);
        else if (strcmp(rule, "LINK") == 0) r_link(n, pj, np, &c, node);
        else if (strcmp(rule, "SAFE") == 0) r_safe(n, pj, np, &c, node);
        else if (strcmp(rule, "CAP") == 0) {
            r_cap(n, pj, np, &c, node);
            cap_check(n, &c, node);
        } else if (strcmp(rule, "SEM") == 0) r_sem(n, pj, np, &c, node);
        else if (strcmp(rule, "COMPOSE") == 0) r_compose(n, pj, np, &c, node);
        else if (strcmp(rule, "PROV") == 0) r_prov(n, pj, np, &c, node);
        else if (strcmp(rule, "REPRO") == 0) r_repro(n, pj, np, &c, node);
        else if (strcmp(rule, "REFINE") == 0) r_refine(n, pj, np, &c, node);
        else fail("unknown rule%s", "");
        c.n++;
    }
    JVal *root = jget(d, "root");
    if (!root || root->kind != J_STR) fail("root is missing%s", "");
    int ri = find_node(&c, root->s);
    if (ri < 0) fail("root is missing%s", "");
    JVal *rj = c.nodes[ri].judgment;
    if (conc_path) {
        size_t cn = 0;
        unsigned char *concb = read_file(conc_path, &cn);
        JVal *conc = json_parse(concb, cn);
        if (!canon_eq(conc, rj)) fail("conclusion does not match the root judgment%s", "");
        free(concb);
    }
    if (!jstr_is(jget(rj, "artifact_sha256"), c.artifact_sha))
        fail("root does not bind the artifact%s", "");
    printf("ACCEPT\n");
    return 0;
}

int main(int argc, char **argv) {
    init_rules_sha();
    if (argc >= 2 && strcmp(argv[1], "rules") == 0) {
        printf("%s\n", RULES_SHA);
        return 0;
    }
    if (argc < 2 || strcmp(argv[1], "verify") != 0) {
        fprintf(stderr, "usage: %s verify --artifact A --derivation D [--conclusion C]\n",
                argv[0]);
        return 2;
    }
    const char *art = NULL, *der = NULL, *conc = NULL;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "--artifact") == 0) art = argv[i + 1];
        else if (strcmp(argv[i], "--derivation") == 0) der = argv[i + 1];
        else if (strcmp(argv[i], "--conclusion") == 0) conc = argv[i + 1];
        else {
            fprintf(stderr, "ERR: unknown flag %s\n", argv[i]);
            return 2;
        }
    }
    if (!art || !der) {
        fprintf(stderr, "ERR: --artifact and --derivation are required\n");
        return 2;
    }
    return do_verify(art, der, conc);
}
